"""审计日志：后台命令端点的只追加操作留痕（对标 ClinInsOS AuditLog 模式）。

设计约束：
- 只追加：不提供任何修改/删除路径；记录内容为「变更要点」而非全量数据；
- 敏感信息（密码、验证码、完整健康档案）不落 detail；
- actor_name 为写入时的冗余快照，账号后续改名不影响历史记录的可读性。
"""

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        # 常用检索路径：按时间倒序翻页（created_at + id 复合，见 essays 同款）
        {"comment": "后台操作审计日志（只追加）"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="admin")
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
