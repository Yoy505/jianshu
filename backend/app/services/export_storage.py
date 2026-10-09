"""后台导出文件的私有目录，必须与公开上传目录隔离。"""

from pathlib import Path

from app.core.config import settings
from app.core.storage_paths import configured_storage_path


def private_export_dir() -> Path:
    directory = configured_storage_path(settings.EXPORT_DIR)
    public_uploads = configured_storage_path(settings.UPLOAD_DIR)
    if directory == public_uploads or public_uploads in directory.parents:
        raise RuntimeError("EXPORT_DIR 不得位于公开 UPLOAD_DIR 内")
    return directory
