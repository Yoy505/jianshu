"""文件上传：multipart，admin/user 任一有效 token 均可。"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.deps import get_current_principal
from app.services.storage import get_storage

router = APIRouter(prefix="/uploads", tags=["uploads"])

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    _principal=Depends(get_current_principal),
) -> dict:
    filename = file.filename or ""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="不支持的文件类型")
    data = await file.read()
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="文件大小不能超过 10MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    url = await get_storage().save(data, filename)
    return {"url": url}
