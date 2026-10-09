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
    OCCUPATIONAL_SUBMISSIONS_ENABLED: bool = False
    FFPROBE_BIN: str = "ffprobe"
    STORAGE_DRIVER: str = "local"  # local | oss

    WX_APPID: str = ""
    WX_SECRET: str = ""

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
