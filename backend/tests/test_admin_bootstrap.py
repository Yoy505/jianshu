"""首次管理员安全开通：正常/重复/弱口令/越权与审计路径（R0 任务 1）。

测试直接在空库上运行 bootstrap（不使用 conftest 的演示种子），
并验证用开通凭据可通过 /admin/auth/login 完成真实登录。
文件型 SQLite + NullPool：每次操作独立连接，避免跨事件循环复用。
"""

import asyncio

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.bootstrap_admin as bootstrap_module
from app.bootstrap_admin import BootstrapError, bootstrap_admin, main
from app.core.deps import get_db
from app.db.base import Base
from app.main import app
from app.models import AdminUser, AuditLog

STRONG_PASSWORD = "Xk7#mQp2$vL9"


@pytest.fixture()
def db_url(tmp_path):
    """空库文件（仅建表，无任何种子），模拟待开通的生产库。"""
    url = f"sqlite+aiosqlite:///{tmp_path / 'bootstrap.db'}"

    async def _create():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.new_event_loop().run_until_complete(_create())
    return url


def _factory(url: str):
    engine = create_async_engine(url, poolclass=NullPool)
    return async_sessionmaker(bind=engine, expire_on_commit=False), engine


async def _admin_count(url: str) -> int:
    factory, engine = _factory(url)
    async with factory() as session:
        count = int(
            await session.scalar(select(func.count()).select_from(AdminUser)) or 0
        )
    await engine.dispose()
    return count


def _admin_count_sync(url: str) -> int:
    """仅供同步 CLI 测试使用（独立事件循环）。"""
    return asyncio.new_event_loop().run_until_complete(_admin_count(url))


@pytest.mark.asyncio
async def test_bootstrap_creates_first_admin_and_login_works(db_url, monkeypatch):
    """正常路径：创建成功 → 审计留痕（无口令）→ 凭据可真实登录。"""
    factory, engine = _factory(db_url)
    status = await bootstrap_admin(
        "ops.zhang", STRONG_PASSWORD, "张运维", session_factory=factory
    )
    assert status == "created"
    assert await _admin_count(db_url) == 1

    async with factory() as session:
        admin = (
            await session.execute(
                select(AdminUser).where(AdminUser.username == "ops.zhang")
            )
        ).scalar_one()
        assert admin.role == "admin"
        assert STRONG_PASSWORD not in admin.password_hash  # 存的是哈希
        audit = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "admin.bootstrap")
            )
        ).scalar_one()
        assert audit.detail == {"username": "ops.zhang", "role": "admin"}
        assert STRONG_PASSWORD not in str(audit.detail)

    # 开通凭据可通过真实登录接口换取 token；错误口令 401
    async def override_get_db():
        async with factory() as session:
            yield session

    monkeypatch.setattr(app, "dependency_overrides", {get_db: override_get_db})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        ok = await client.post(
            "/api/v1/admin/auth/login",
            json={"username": "ops.zhang", "password": STRONG_PASSWORD},
        )
        bad = await client.post(
            "/api/v1/admin/auth/login",
            json={"username": "ops.zhang", "password": "wrong-password-x"},
        )
    await engine.dispose()
    assert ok.status_code == 200
    assert ok.json()["admin"]["role"] == "admin"
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_refuses_when_admin_already_exists(db_url):
    """重复执行保护：已有管理员（无论用户名是否相同）一律拒绝，无副作用。"""
    factory, engine = _factory(db_url)
    await bootstrap_admin(
        "first.admin", STRONG_PASSWORD, "第一管理员", session_factory=factory
    )

    with pytest.raises(BootstrapError, match="已存在 1 个管理员账号"):
        await bootstrap_admin(
            "second.admin", "An0ther$trong99", "第二管理员", session_factory=factory
        )
    await engine.dispose()

    assert await _admin_count(db_url) == 1
    # 拒绝路径未追加审计（仅首次创建那一条）
    async with factory() as session:
        audits = int(
            await session.scalar(select(func.count()).select_from(AuditLog)) or 0
        )
    assert audits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_password",
    [
        "Sh0rt!x",                 # 过短
        "admin12345678",           # 弱口令样本
        "alllowercase123456",      # 仅两类字符
        "NOLOWERNOEXCLAIM1",       # 缺小写
        "ops.zhang" + "Aa1!" * 3,  # 包含用户名
    ],
)
async def test_bootstrap_rejects_weak_passwords(db_url, bad_password):
    factory, engine = _factory(db_url)
    with pytest.raises(BootstrapError):
        await bootstrap_admin(
            "ops.zhang", bad_password, "张运维", session_factory=factory
        )
    await engine.dispose()
    assert await _admin_count(db_url) == 0


@pytest.mark.asyncio
async def test_bootstrap_rejects_bad_username(db_url):
    factory, engine = _factory(db_url)
    for bad in ("", "ab", "含中文", "has space", "x" * 51):
        with pytest.raises(BootstrapError):
            await bootstrap_admin(
                bad, STRONG_PASSWORD, "张运维", session_factory=factory
            )
    await engine.dispose()
    assert await _admin_count(db_url) == 0


def test_cli_success_and_repeat(db_url, monkeypatch, capsys):
    """CLI：环境变量读取、退出码 0/1、重复执行拒绝。"""
    factory, engine = _factory(db_url)
    monkeypatch.setattr(bootstrap_module, "AsyncSessionLocal", factory)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "ops.li")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_NAME", "李运维")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", STRONG_PASSWORD)

    assert main() == 0
    assert "ops.li" in capsys.readouterr().out

    # 重复执行：退出码 1，错误信息说明原因
    assert main() == 1
    err = capsys.readouterr().err
    assert "已拒绝" in err and "已存在" in err
    assert _admin_count_sync(db_url) == 1
    asyncio.new_event_loop().run_until_complete(engine.dispose())


def test_cli_missing_env_fails_without_side_effect(db_url, monkeypatch, capsys):
    factory, engine = _factory(db_url)
    monkeypatch.setattr(bootstrap_module, "AsyncSessionLocal", factory)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD_FILE", raising=False)

    assert main() == 1
    assert "未设置" in capsys.readouterr().err
    assert _admin_count_sync(db_url) == 0
    asyncio.new_event_loop().run_until_complete(engine.dispose())


def test_cli_password_file(db_url, monkeypatch, capsys, tmp_path):
    """口令可经密钥文件提供（容器/secret 挂载场景）。"""
    factory, engine = _factory(db_url)
    monkeypatch.setattr(bootstrap_module, "AsyncSessionLocal", factory)
    pw_file = tmp_path / "bootstrap_pw"
    pw_file.write_text(STRONG_PASSWORD + "\n")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "ops.file")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_NAME", "文件口令")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD_FILE", str(pw_file))
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD", raising=False)

    assert main() == 0
    assert _admin_count_sync(db_url) == 1
    asyncio.new_event_loop().run_until_complete(engine.dispose())
