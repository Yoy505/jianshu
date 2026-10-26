"""应用配置：pydantic-settings，支持环境变量与 .env 文件。"""

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.storage_paths import configured_storage_path


DEFAULT_JWT_SECRET = "dev-only-secret-please-change-me-in-production-48ch"


class Settings(BaseSettings):
    APP_NAME: str = "杏临汇服务平台"
    APP_ENV: Literal["dev", "test", "staging", "prod"] = "staging"

    DATABASE_URL: str = "postgresql+asyncpg://jianshu:jianshu@localhost:5432/jianshu"
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    JWT_SECRET: str = DEFAULT_JWT_SECRET
    JWT_ACCESS_TOKEN_MINUTES: int = 120
    JWT_REFRESH_TOKEN_DAYS: int = 7

    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
    ]

    UPLOAD_DIR: str = "./uploads"
    EXPORT_DIR: str = "./private_exports"  # 后台导出；不得置于公开 UPLOAD_DIR 内
    OCCUPATIONAL_SUBMISSION_DIR: str = "./private_occupational_submissions"
    DRUG_LEAFLET_DIR: str = "./private_drug_leaflets"
    DRUG_LEAFLET_INTAKE_DIR: str = "./private_drug_leaflet_intake"
    DRUG_REGULATORY_SOURCE_DIR: str = "./private_drug_regulatory_sources"
    COMMERCE_BATCH_DIR: str = "./private_commerce_batches"  # 单位名单原件；不得位于公开 UPLOAD_DIR 内
    COMMERCE_BATCH_FILE_MAX_BYTES: int = 2 * 1024 * 1024  # 名单原件上限 2 MiB
    COMMERCE_BATCH_MAX_ROWS: int = 10000
    OCCUPATIONAL_SUBMISSIONS_ENABLED: bool = False
    VOLUNTEER_APPLICATIONS_ENABLED: bool = False
    FFPROBE_BIN: str = "ffprobe"
    STORAGE_DRIVER: str = "local"  # local | oss

    WX_APPID: str = ""
    WX_SECRET: str = ""

    # 微信支付 v3（工作一服务包交易）。全部配置齐全才开放微信支付；缺任一项时
    # 下单/预支付/查单/退款返回 503，回调拒绝并审计。私钥与平台证书只从文件读取。
    WXPAY_MCHID: str = ""
    WXPAY_API_V3_KEY: str = ""
    WXPAY_CERT_SERIAL_NO: str = ""
    WXPAY_PRIVATE_KEY_PATH: str = ""
    WXPAY_PLATFORM_CERT_PATH: str = ""
    WXPAY_NOTIFY_URL: str = ""
    # 待支付订单超时（分钟）；超时后懒更新为 closed，支付成功晚到仍可入账
    COMMERCE_ORDER_TTL_MINUTES: int = 30

    # 服务履约（工作二，2026-10-22）。服务期天数在激活时快照到实例。
    # 授权条款自批次一起以 steward_consent_terms 表管理（后台发布版本、
    # 展示-确认-版本校验）；原 STEWARD_*_CONSENT_TEXT 配置项已废止，残留环境变量不生效。
    # 条款未发布时授权 503 待配置、门禁恒 pending；试点放行表无有效记录时激活一律阻断。
    STEWARD_SERVICE_DAYS: int = 45
    STEWARD_FOLLOWUP_DAYS: str = "7,14,21,28,35,42"
    # 触达（切片 B）：人工驳回重办次数上限，达到后意图置 failed（失败队列终态）
    STEWARD_OUTREACH_MAX_ATTEMPTS: int = 3
    # 设备云数据（切片 C）：晚到判定窗口（天）与连续同步失败上限（达限 blocked 须人工重置）
    STEWARD_DEVICE_LATE_DAYS: int = 7
    STEWARD_DEVICE_MAX_SYNC_FAILURES: int = 5
    # CCC 电话通路（切片 E）：真实外呼关闭；重试上限与间隔；回调验签密钥（空=拒收）
    STEWARD_PHONE_MAX_ATTEMPTS: int = 3
    STEWARD_PHONE_RETRY_DELAY_MINUTES: int = 30
    STEWARD_PHONE_CCC_SECRET: str = ""

    # 阿里云 OSS（生产预留）
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_ENDPOINT: str = ""
    OSS_BUCKET_NAME: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @model_validator(mode="after")
    def reject_unsafe_public_config(self) -> "Settings":
        upload_dir = configured_storage_path(self.UPLOAD_DIR)
        submission_dir = configured_storage_path(self.OCCUPATIONAL_SUBMISSION_DIR)
        if submission_dir == upload_dir or submission_dir.is_relative_to(upload_dir):
            raise ValueError("职业健康投稿私有目录不能位于公开 UPLOAD_DIR 内")
        leaflet_dir = configured_storage_path(self.DRUG_LEAFLET_DIR)
        if leaflet_dir == upload_dir or leaflet_dir.is_relative_to(upload_dir):
            raise ValueError("药品说明书私有目录不能位于公开 UPLOAD_DIR 内")
        intake_dir = configured_storage_path(self.DRUG_LEAFLET_INTAKE_DIR)
        if intake_dir == upload_dir or intake_dir.is_relative_to(upload_dir):
            raise ValueError("药品说明书待核目录不能位于公开 UPLOAD_DIR 内")
        if (
            intake_dir == leaflet_dir or intake_dir.is_relative_to(leaflet_dir)
            or leaflet_dir.is_relative_to(intake_dir)
        ):
            raise ValueError("药品说明书待核目录必须与既有说明书私有目录隔离")
        regulatory_dir = configured_storage_path(self.DRUG_REGULATORY_SOURCE_DIR)
        if regulatory_dir == upload_dir or regulatory_dir.is_relative_to(upload_dir):
            raise ValueError("药品监管来源私有目录不能位于公开 UPLOAD_DIR 内")
        batch_dir = configured_storage_path(self.COMMERCE_BATCH_DIR)
        if batch_dir == upload_dir or batch_dir.is_relative_to(upload_dir):
            raise ValueError("单位名单私有目录不能位于公开 UPLOAD_DIR 内")
        if self.APP_ENV in {"dev", "test"}:
            return self

        secret = self.JWT_SECRET.strip()
        if (
            len(secret) < 32
            or secret == DEFAULT_JWT_SECRET
            or "change-me" in secret.lower()
            or secret.lower().startswith(("dev-", "test-"))
        ):
            raise ValueError("预生产/生产环境必须设置至少 32 位、非示例值的 JWT_SECRET")
        if not self.WX_APPID.strip() or not self.WX_SECRET.strip():
            raise ValueError("预生产/生产环境必须配置 WX_APPID 和 WX_SECRET")
        return self


settings = Settings()
