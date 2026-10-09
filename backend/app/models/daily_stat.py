import datetime

from sqlalchemy import Date, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DailyStat(TimestampMixin, Base):
    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("date", name="uq_daily_stats_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    users_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    essays_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    likes_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DailyStat {self.date}>"
