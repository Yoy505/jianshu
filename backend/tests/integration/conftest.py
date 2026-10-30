"""真实基础设施集成入口（T4）：隔离 PostgreSQL / Redis / Celery worker。

默认跳过；仅在显式注入本机隔离实例时运行（防误连生产）：
    export JIANSHU_PG_TEST_URL='postgresql+asyncpg://jianshu:jianshu@127.0.0.1:54329/jianshu_int_test'
    export JIANSHU_REDIS_TEST_URL='redis://127.0.0.1:6399/15'
    .venv-py315/bin/python -m pytest tests/integration -v

防误连保护：PG 仅允许本机 host 且库名含 test/int；Redis 仅允许本机且 db 序号 ≥ 10。
全部键使用 jianshu-int: 前缀并在结束时只清理该前缀。
"""

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
VENV_BIN = BACKEND_DIR / ".venv-py315" / "bin"

PG_URL = os.environ.get("JIANSHU_PG_TEST_URL", "")
REDIS_URL = os.environ.get("JIANSHU_REDIS_TEST_URL", "")
KEY_PREFIX = "jianshu-int:"


def _guard_pg(url: str) -> None:
    u = make_url(url)
    assert u.get_backend_name() in {"postgresql", "postgresql+asyncpg"}, "集成 PG 入口只接受 postgresql 连接串"
    assert (u.host or "") in {"127.0.0.1", "localhost"}, f"防误连生产：PG host 必须本机（当前 {u.host}）"
    db = (u.database or "").lower()
    assert "test" in db or "int" in db, f"防误连生产：PG 库名须含 test/int（当前 {db}）"


def _guard_redis(url: str) -> None:
    u = make_url(url)
    assert (u.host or "") in {"127.0.0.1", "localhost"}, f"防误连生产：Redis host 必须本机（当前 {u.host}）"
    try:
        db_index = int(u.database or 0)
    except ValueError:
        db_index = 0
    assert db_index >= 10, f"防误连生产：Redis db 序号须 ≥10（当前 {db_index}）"


if PG_URL:
    _guard_pg(PG_URL)
if REDIS_URL:
    _guard_redis(REDIS_URL)

# Celery 应用需在注入 broker 环境后再导入（根 conftest 未触碰 app.workers）
if REDIS_URL:
    os.environ["CELERY_BROKER_URL"] = REDIS_URL
    os.environ["CELERY_RESULT_BACKEND"] = REDIS_URL


def alembic_run(*args: str, url: str) -> str:
    """以子进程执行 alembic（干净进程内 Settings 读注入的 DATABASE_URL）。"""
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "test"}
    proc = subprocess.run(
        [str(VENV_BIN / "python"), "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=True,
    )
    return proc.stdout + proc.stderr


def expected_migration_head() -> str:
    """迁移配置的有效目标头（单头；多头说明需要先合并）。断言不再写死修订号。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"迁移存在多个头，需先合并: {heads}"
    return heads[0]


@contextmanager
def scratch_database(suffix: str):
    """在同一隔离 PG 实例上建一个一次性库（名称沿用隔离库名 + 后缀，仍受防误连守卫约束）。"""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    base = make_url(PG_URL)
    name = f"{base.database}_{suffix}"
    _guard_pg(str(base.set(database=name)))
    scratch_url = str(base.set(database=name))

    async def _admin(sql: str) -> None:
        engine = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(sql))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}"'))
        asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # 无 CREATEDB 权限等：如实跳过，不伪装通过
        pytest.skip(f"隔离 PG 无法建一次性库（{type(exc).__name__}: {exc}）")
    try:
        yield scratch_url
    finally:
        asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}"'))


def pg_column_count(url: str, table: str, columns: tuple[str, ...]) -> int:
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _count() -> int:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                row = await conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = ANY(:cols)"
                    ),
                    {"t": table, "cols": list(columns)},
                )
                return int(row.scalar_one())
        finally:
            await engine.dispose()

    return asyncio.run(_count())


# ───────────────────────── PostgreSQL ─────────────────────────


@pytest_asyncio.fixture(scope="session")
async def pg_engine():
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    alembic_run("upgrade", "head", url=PG_URL)
    engine = create_async_engine(PG_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_client(pg_engine):
    """对 app 的 httpx 客户端，get_db 指向真实 PG。"""
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.main as app_main
    from app.core.deps import get_db, get_redis
    from app.core.security import hash_password
    from app.models import AdminUser, User
    from tests.conftest import FakeRedis

    factory = async_sessionmaker(bind=pg_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    app_main.app.dependency_overrides[get_db] = override_get_db
    app_main.app.dependency_overrides[get_redis] = lambda: FakeRedis()

    async with factory() as session:
        existing = (await session.execute(select_admin())).scalars().first()
        if existing is None:
            session.add_all([
                AdminUser(username="admin", name="管理员", password_hash=hash_password("admin123"), role="admin"),
                AdminUser(username="editor", name="编辑", password_hash=hash_password("editor123"), role="editor"),
                AdminUser(username="finance", name="财务", password_hash=hash_password("finance12345"), role="finance"),
                User(nickname="集成用户", phone="13800000001", status="active"),
            ])
            await session.commit()

    transport = httpx.ASGITransport(app=app_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://integration-test") as client:
        yield client
    app_main.app.dependency_overrides.clear()


def select_admin():
    from sqlalchemy import select

    from app.models import AdminUser

    return select(AdminUser).where(AdminUser.username == "admin")


# ───────────────────────── Redis ─────────────────────────


@pytest_asyncio.fixture
async def real_redis():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_URL, decode_responses=True)
    await client.ping()
    yield client
    # 只清理本套件的键（前缀隔离，不做 FLUSHDB）
    keys = []
    async for key in client.scan_iter(match=f"{KEY_PREFIX}*"):
        keys.append(key)
    if keys:
        await client.delete(*keys)
    await client.aclose()


# ───────────────────────── Celery（跨进程真实 worker） ─────────────────────────


@pytest.fixture(scope="session")
def celery_worker():
    """以真实 Redis broker 启动独立 celery worker 子进程（solo 池便于稳定收尾）。"""
    if not (PG_URL and REDIS_URL):
        pytest.skip("Celery 集成需要 JIANSHU_PG_TEST_URL 与 JIANSHU_REDIS_TEST_URL")
    env = {
        **os.environ,
        "APP_ENV": "test",
        "DATABASE_URL": PG_URL,
        "REDIS_URL": REDIS_URL,
        "CELERY_BROKER_URL": REDIS_URL,
        "CELERY_RESULT_BACKEND": REDIS_URL,
    }
    proc = subprocess.Popen(
        [
            str(VENV_BIN / "celery"), "-A", "app.workers.celery_app", "worker",
            "--pool=solo", "-l", "warning", "--without-gossip", "--without-mingle",
        ],
        cwd=BACKEND_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        from app.workers.celery_app import celery_app

        deadline = time.time() + 60
        while time.time() < deadline:
            replies = celery_app.control.inspect(timeout=1).ping()
            if replies:
                break
            if proc.poll() is not None:
                raise RuntimeError("celery worker 启动即退出")
            time.sleep(1)
        else:
            raise RuntimeError("celery worker 60s 内未就绪")
        yield celery_app
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
