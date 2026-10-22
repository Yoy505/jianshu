"""pytest 公共夹具：SQLite 内存库 + get_db/get_redis 覆盖（不依赖外部 PG/Redis）。"""

import os
import time

# 必须在导入 app 之前显式设置 test，避免安全默认环境拒绝示例密钥，且不连接真实 Postgres
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["REDIS_URL"] = "redis://localhost:6379/9"
os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/9"
os.environ["CELERY_RESULT_BACKEND"] = "redis://localhost:6379/9"

from datetime import datetime, timedelta, timezone  # noqa: E402

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.deps import get_db, get_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminUser, Contest, Essay, User  # noqa: E402
from app.seed import seed_mp_data  # noqa: E402


class FakeRedis:
    """覆盖后端用到的 Redis 命令子集的内存桩（不引入 fakeredis 依赖）。"""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._expire: dict[str, float] = {}

    def _sweep(self, key: str) -> None:
        exp = self._expire.get(key)
        if exp is not None and time.monotonic() >= exp:
            self._data.pop(key, None)
            self._expire.pop(key, None)

    async def get(self, key: str):
        self._sweep(key)
        return self._data.get(key)

    async def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        if nx:
            self._sweep(key)
            if key in self._data:
                return None
        self._data[key] = value
        if ex is not None:
            self._expire[key] = time.monotonic() + ex
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data:
                removed += 1
            self._data.pop(key, None)
            self._expire.pop(key, None)
        return removed

    async def incr(self, key: str) -> int:
        value = int(self._data.get(key, 0)) + 1
        self._data[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self._expire[key] = time.monotonic() + max(int(seconds), 0)
        return True

    async def ttl(self, key: str) -> int:
        self._sweep(key)
        if key not in self._data:
            return -2
        if key not in self._expire:
            return -1
        return max(0, int(self._expire[key] - time.monotonic()))


async def _seed_test_data(session) -> None:
    now = datetime.now(timezone.utc)
    admin = AdminUser(
        username="admin",
        name="系统管理员",
        password_hash=hash_password("admin123"),
        role="admin",
    )
    editor = AdminUser(
        username="editor",
        name="内容编辑",
        password_hash=hash_password("editor123"),
        role="editor",
    )
    user = User(nickname="测试用户", phone="13800000001", status="active")
    contest = Contest(
        title="测试征文",
        description="用于后台流程测试",
        rules="无",
        prizes="无",
        status="published",
        start_at=now - timedelta(days=5),
        end_at=now + timedelta(days=5),
    )
    session.add_all([admin, editor, user, contest])
    await session.flush()
    session.add(
        Essay(
            contest_id=contest.id,
            user_id=user.id,
            title="待审核的文章",
            content="这是用于后台审核流程测试的文章内容。" * 10,
            status="submitted",
        )
    )
    # 健康域（/mp）测试数据：复用 seed.py 的幂等种子（含演示用户 13800000001 的档案/预约/会话）
    await seed_mp_data(session)
    await session.commit()


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    redis_stub = FakeRedis()

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: redis_stub

    async with session_factory() as session:
        await _seed_test_data(session)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        # 挂同库会话工厂，供测试做 ORM 级数据准备 / 时间推进（ steward 等域）
        test_client.session_factory = session_factory  # type: ignore[attr-defined]
        yield test_client

    app.dependency_overrides.clear()
    await engine.dispose()


# 工作二履约域夹具（条款文本配置）注册进全局命名空间
from tests.steward_helpers import file_db_steward, steward_env  # noqa: E402,F401
