"""文件存储抽象：本地磁盘 / 阿里云 OSS（生产预留）。"""

from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.storage_paths import configured_storage_path


class LocalStorage:
    """本地开发存储：写入 UPLOAD_DIR，返回 /uploads/<文件名> 访问路径。"""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = configured_storage_path(base_dir)

    async def save(self, data: bytes, original_filename: str) -> str:
        ext = Path(original_filename or "").suffix.lower()
        name = f"{uuid4().hex}{ext}"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / name).write_bytes(data)
        return f"/uploads/{name}"


class OssStorage:
    """阿里云 OSS 驱动（预留）：接入后在此实现对象写入与签名 URL。"""

    def __init__(self) -> None:
        self.access_key_id = settings.OSS_ACCESS_KEY_ID
        self.access_key_secret = settings.OSS_ACCESS_KEY_SECRET
        self.endpoint = settings.OSS_ENDPOINT
        self.bucket = settings.OSS_BUCKET_NAME

    async def save(self, data: bytes, original_filename: str) -> str:
        raise NotImplementedError("OSS 存储驱动尚未接入，请将 STORAGE_DRIVER 设为 local")


def get_storage() -> LocalStorage | OssStorage:
    if settings.STORAGE_DRIVER == "oss":
        return OssStorage()
    return LocalStorage(settings.UPLOAD_DIR)
