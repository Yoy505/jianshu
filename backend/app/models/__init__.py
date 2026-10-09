"""SQLAlchemy 模型汇总：导入即注册到 Base.metadata（Alembic / create_all 依赖）。"""

from app.db.base import Base, TimestampMixin
from app.models.admin_user import AdminRole, AdminUser
from app.models.audit import AuditLog
from app.models.contest import Contest, ContestStatus
from app.models.daily_stat import DailyStat
from app.models.essay import Essay, EssayLike, EssayReview, EssayStatus, ReviewAction
from app.models.mp import (
    MP_DRUG_TAGS,
    MP_DRUG_PUBLICATION_STATUSES,
    MP_PERIODS,
    MpAppointment,
    MpAppointmentStatus,
    MpConsult,
    MpConsultMessage,
    MpConsultStatus,
    MpDrug,
    MpDrugLeaflet,
    MpDrugLeafletIntake,
    MpDrugRegulatoryObservation,
    MpDrugRegulatorySource,
    MpDrugFeedback,
    MpFavorite,
    MpNotice,
    MpProduct,
    MpProfile,
    MpProvider,
    MpProviderService,
    MpReview,
    MpScenario,
    MpService,
    MpServiceType,
)
from app.models.occupational import OccupationalReviewRecord, OccupationalSubmission
from app.models.user import User, UserStatus

__all__ = [
    "Base",
    "TimestampMixin",
    "AdminRole",
    "AdminUser",
    "AuditLog",
    "Contest",
    "ContestStatus",
    "DailyStat",
    "Essay",
    "EssayLike",
    "EssayReview",
    "EssayStatus",
    "ReviewAction",
    "OccupationalSubmission",
    "OccupationalReviewRecord",
    "User",
    "UserStatus",
    # 健康服务域（杏临汇小程序）
    "MP_DRUG_TAGS",
    "MP_DRUG_PUBLICATION_STATUSES",
    "MP_PERIODS",
    "MpAppointment",
    "MpAppointmentStatus",
    "MpConsult",
    "MpConsultMessage",
    "MpConsultStatus",
    "MpDrug",
    "MpDrugLeaflet",
    "MpDrugLeafletIntake",
    "MpDrugRegulatoryObservation",
    "MpDrugRegulatorySource",
    "MpDrugFeedback",
    "MpFavorite",
    "MpNotice",
    "MpProduct",
    "MpProfile",
    "MpProvider",
    "MpProviderService",
    "MpReview",
    "MpScenario",
    "MpService",
    "MpServiceType",
]
