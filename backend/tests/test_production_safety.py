"""未显式开发/测试时失败关闭：配置、演示种子和未接入的登录通道。"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.config import DEFAULT_JWT_SECRET, Settings, settings
from app.seed import seed, seed_mp_data
from app.services.sms_service import issue_code, verify_code
from app.services.wx_service import code2session
from app.workers.tasks import send_sms
from tests.conftest import FakeRedis


PRODUCTION_SECRET = "a4c1bb1d9b8f4fc78ee11b6d6cfb368b607f8141e017495c"


def test_unset_app_env_defaults_to_safe_staging(monkeypatch):
    for name in ("APP_ENV", "JWT_SECRET", "WX_APPID", "WX_SECRET"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None)


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_public_environments_reject_default_or_example_jwt_secret(environment):
    for secret in (DEFAULT_JWT_SECRET, "please-change-me-in-production", "short"):
        with pytest.raises(ValidationError, match="JWT_SECRET"):
            Settings(
                _env_file=None,
                APP_ENV=environment,
                JWT_SECRET=secret,
                WX_APPID="wx123",
                WX_SECRET="wechat-secret",
            )


@pytest.mark.parametrize("environment", ["staging", "prod"])
@pytest.mark.parametrize("appid,wx_secret", [("", "wechat-secret"), ("wx123", ""), ("  ", "wechat-secret")])
def test_public_environments_require_wechat_config(environment, appid, wx_secret):
    with pytest.raises(ValidationError, match="WX_APPID 和 WX_SECRET"):
        Settings(
            _env_file=None,
            APP_ENV=environment,
            JWT_SECRET=PRODUCTION_SECRET,
            WX_APPID=appid,
            WX_SECRET=wx_secret,
        )


def test_valid_public_config_and_explicit_demo_environments():
    for environment in ("staging", "prod"):
        configured = Settings(
            _env_file=None,
            APP_ENV=environment,
            JWT_SECRET=PRODUCTION_SECRET,
            WX_APPID="wx123",
            WX_SECRET="wechat-secret",
        )
        assert configured.APP_ENV == environment
    for environment in ("dev", "test"):
        demo = Settings(
            _env_file=None,
            APP_ENV=environment,
            JWT_SECRET=DEFAULT_JWT_SECRET,
            WX_APPID="",
            WX_SECRET="",
        )
        assert demo.APP_ENV == environment


@pytest.mark.asyncio
async def test_demo_seed_rejects_public_environments_before_database_access(monkeypatch):
    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        with pytest.raises(RuntimeError, match="演示种子仅可"):
            await seed()
        with pytest.raises(RuntimeError, match="演示种子仅可"):
            await seed_mp_data(object())


@pytest.mark.asyncio
async def test_dev_code_is_explicit_demo_only(monkeypatch):
    monkeypatch.setattr(settings, "WX_APPID", "")
    monkeypatch.setattr(settings, "WX_SECRET", "")
    for environment in ("dev", "test"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        assert (await code2session("dev-code"))["openid"].startswith("dev_")
    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        with pytest.raises(HTTPException) as exc:
            await code2session("dev-code")
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_unavailable_public_sms_does_not_create_or_accept_code(monkeypatch):
    phone = "13800000001"
    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        redis = FakeRedis()
        with pytest.raises(HTTPException) as exc:
            await issue_code(redis, phone)
        assert exc.value.status_code == 503
        assert redis._data == {}

        await redis.set(f"sms:{phone}:code", "123456#0", ex=300)
        with pytest.raises(HTTPException) as exc:
            await verify_code(redis, phone, "123456")
        assert exc.value.status_code == 503

        with pytest.raises(RuntimeError, match="验证码未发送"):
            send_sms.run(phone, "123456")


@pytest.mark.asyncio
async def test_explicit_demo_sms_can_issue_and_verify_code(monkeypatch):
    monkeypatch.setattr(send_sms, "delay", lambda *args: None)
    for environment in ("dev", "test"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        redis = FakeRedis()
        code = await issue_code(redis, "13800000001")
        assert len(code) == 6
        await verify_code(redis, "13800000001", code)
        assert await redis.get("sms:13800000001:code") is None
        assert send_sms.run("13800000001", code)["sent"] is True


@pytest.mark.asyncio
async def test_public_sms_endpoint_returns_failure(client, monkeypatch):
    for environment in ("staging", "prod"):
        monkeypatch.setattr(settings, "APP_ENV", environment)
        response = await client.post("/api/v1/h5/auth/sms/send", json={"phone": "13800000001"})
        assert response.status_code == 503
        assert "验证码已发送" not in response.text

        response = await client.post(
            "/api/v1/h5/auth/sms/login",
            json={"phone": "13800000001", "code": "123456"},
        )
        assert response.status_code == 503


def test_docs_disabled_in_public_environments(monkeypatch):
    """D4 加固：staging/prod 下 /docs、/redoc、/openapi.json 均不可用（404）。"""
    import importlib

    from fastapi.testclient import TestClient

    import app.core.config as app_config
    from app import main as app_main

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("JWT_SECRET", PRODUCTION_SECRET)
    monkeypatch.setenv("WX_APPID", "wx-demo")
    monkeypatch.setenv("WX_SECRET", "demo-secret")
    # main 的 _PUBLIC_ENV 在模块加载时求值：先重载 config（重建 Settings）再重载 main
    importlib.reload(app_config)
    reloaded = importlib.reload(app_main)
    try:
        with TestClient(reloaded.app) as client:
            for path in ("/docs", "/redoc", "/openapi.json"):
                resp = client.get(path)
                assert resp.status_code == 404, f"{path} 应在 staging 关闭，实际 {resp.status_code}"
            health = client.get("/health")
            assert health.status_code == 200
    finally:
        # 恢复为测试环境配置，避免污染同进程其他用例
        monkeypatch.setenv("APP_ENV", "test")
        importlib.reload(app_config)
        importlib.reload(app_main)


def test_docs_available_in_dev_environment(monkeypatch):
    """dev 环境保留 /docs 便于联调。"""
    import importlib

    from fastapi.testclient import TestClient

    import app.core.config as app_config
    from app import main as app_main

    monkeypatch.setenv("APP_ENV", "dev")
    importlib.reload(app_config)
    reloaded = importlib.reload(app_main)
    try:
        with TestClient(reloaded.app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        importlib.reload(app_config)
        importlib.reload(app_main)
