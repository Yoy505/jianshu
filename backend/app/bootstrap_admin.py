"""一次性安全开通首个后台管理员。

设计目标（对应总体计划 R0 任务 1 / 审核报告 P0「固定口令 seed」订正项）：

- 仅当数据库中不存在任何管理员时才创建；已有管理员时一律拒绝（重复执行保护）；
- 凭据只从环境变量或密钥文件读取，不经命令行参数（避免进入 shell history），
  不写入日志与审计 detail；
- 口令策略：≥12 位，至少含大写/小写/数字/特殊字符中的三类，不得包含用户名，
  不得命中常见弱口令样本；
- PostgreSQL 下使用事务级咨询锁串行化并发开通；SQLite（测试）退化为
  事务内计数保护；
- 创建成功写入审计日志（actor_type=system，action=admin.bootstrap），
  detail 只含用户名与角色，不含口令。

用法（生产服务器，在 backend/ 目录、已加载生产 .env 的前提下）：

    export BOOTSTRAP_ADMIN_USERNAME="ops.zhang"
    export BOOTSTRAP_ADMIN_NAME="张三"
    export BOOTSTRAP_ADMIN_PASSWORD='...'          # 或
    export BOOTSTRAP_ADMIN_PASSWORD_FILE=/etc/jianshu/bootstrap_pw
    python -m app.bootstrap_admin                  # 退出码 0=已创建，1=拒绝/失败

重复执行、缺参、弱口令均以非零退出码失败，不做任何副作用。
丢失全部管理员不属于本命令职责，需按部署文档的数据库人工恢复流程处理。
"""

import asyncio
import os
import re
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.db.session import AsyncSessionLocal
from app.models import AdminUser, AuditLog

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
# 常见弱口令样本（小写比较）；仅为最后兜底，强度策略才是主闸门
WEAK_PASSWORD_SAMPLES = (
    "admin123",
    "admin123456",
    "password",
    "password123",
    "12345678",
    "123456789012",
    "qwertyuiopas",
    "888888888888",
    "666666666666",
    "abcd12341234",
)

ADVISORY_LOCK_KEY = "app:bootstrap_admin"


class BootstrapError(RuntimeError):
    """开通被拒绝或环境不满足；CLI 以非零退出码结束，不做任何写入。"""


def _password_char_classes(password: str) -> int:
    classes = 0
    if re.search(r"[A-Z]", password):
        classes += 1
    if re.search(r"[a-z]", password):
        classes += 1
    if re.search(r"[0-9]", password):
        classes += 1
    if re.search(r"[^A-Za-z0-9]", password):
        classes += 1
    return classes


def validate_bootstrap_credentials(username: str, password: str, name: str) -> None:
    """校验开通凭据；任何不满足都以 BootstrapError 拒绝，不做纠正性修改。"""
    if not USERNAME_RE.fullmatch(username or ""):
        raise BootstrapError(
            "用户名须为 3-50 位字母、数字、下划线、点或短横线（不得使用中文或空格）"
        )
    if not (1 <= len(name or "") <= 50):
        raise BootstrapError("显示名称须为 1-50 个字符")

    if len(password) < 12:
        raise BootstrapError("口令长度不得少于 12 位")
    if password.lower() in WEAK_PASSWORD_SAMPLES:
        raise BootstrapError("口令命中常见弱口令样本，请更换")
    if username.lower() in password.lower():
        raise BootstrapError("口令不得包含用户名")
    if _password_char_classes(password) < 3:
        raise BootstrapError("口令须至少包含大写字母、小写字母、数字、特殊字符中的三类")


async def bootstrap_admin(
    username: str,
    password: str,
    name: str,
    *,
    session_factory: async_sessionmaker | None = None,
) -> str:
    """创建首个管理员；返回 "created"。

    已存在任何管理员、凭据不合法或数据库写入失败时抛 BootstrapError，
    事务回滚，不产生半写入状态。session_factory 参数仅供测试注入。
    """
    validate_bootstrap_credentials(username, password, name)
    factory = session_factory or AsyncSessionLocal
    password_hash = hash_password(password)

    async with factory() as session:  # type: AsyncSession
        async with session.begin():
            # PostgreSQL：事务级咨询锁串行化并发的首次开通；SQLite（测试）跳过
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                await session.execute(
                    text(f"SELECT pg_advisory_xact_lock(hashtext('{ADVISORY_LOCK_KEY}'))")
                )
            existing = await session.scalar(select(func.count()).select_from(AdminUser))
            if existing:
                raise BootstrapError(
                    f"已存在 {int(existing)} 个管理员账号，首次开通仅允许在空库执行一次；"
                    "如需追加账号请由现有管理员在后台操作"
                )
            admin = AdminUser(
                username=username,
                name=name,
                password_hash=password_hash,
                role="admin",
            )
            session.add(admin)
            await session.flush()  # 取得自增 id，供审计引用
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=None,
                    actor_name="bootstrap",
                    action="admin.bootstrap",
                    resource_type="admin_user",
                    resource_id=admin.id,
                    # 口令与口令哈希不得进入审计
                    detail={"username": username, "role": "admin"},
                    ip_address=None,
                )
            )
    return "created"


def _read_password_from_env() -> tuple[str | None, str | None]:
    """从环境变量或密钥文件读取口令；(口令, 错误消息)。"""
    password_file = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD_FILE", "").strip()
    if password_file:
        try:
            with open(password_file, "r", encoding="utf-8") as fh:
                return fh.read().strip(), None
        except OSError as exc:
            return None, f"无法读取口令文件 {password_file}：{exc}"
    return os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", ""), None


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；返回进程退出码。凭据缺失/校验失败/重复执行均返回 1。"""
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "").strip()
    name = os.environ.get("BOOTSTRAP_ADMIN_NAME", "").strip() or username
    password, file_error = _read_password_from_env()
    if file_error:
        print(f"[bootstrap] 失败：{file_error}", file=sys.stderr)
        return 1

    if not username:
        print(
            "[bootstrap] 失败：未设置 BOOTSTRAP_ADMIN_USERNAME；"
            "口令经 BOOTSTRAP_ADMIN_PASSWORD 或 BOOTSTRAP_ADMIN_PASSWORD_FILE 提供",
            file=sys.stderr,
        )
        return 1
    if not password:
        print(
            "[bootstrap] 失败：未设置 BOOTSTRAP_ADMIN_PASSWORD"
            "（或 BOOTSTRAP_ADMIN_PASSWORD_FILE）",
            file=sys.stderr,
        )
        return 1

    try:
        status = asyncio.run(bootstrap_admin(username, password, name))
    except BootstrapError as exc:
        print(f"[bootstrap] 已拒绝：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # 数据库未迁移/连接失败等：如实报告，退出码 1
        print(
            f"[bootstrap] 失败：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        if "no such table" in str(exc).lower():
            print(
                "[bootstrap] 提示：数据库尚无表结构，请先执行 `alembic upgrade head`。",
                file=sys.stderr,
            )
        return 1

    print(
        f"[bootstrap] {status}：首个管理员 {username}（角色 admin）已创建。"
        "口令不会出现在任何日志中；本命令再次执行将被拒绝。"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
