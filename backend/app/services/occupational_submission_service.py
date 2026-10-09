"""职业健康投稿文件的私有保存与实际内容校验。"""

import hashlib
import json
import math
import re
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from fastapi import HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.storage_paths import configured_storage_path

ARTICLE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 300 * 1024 * 1024
# 2026-10-08 按征集方要求取消字数与时长门槛：服务端仍统计字数/时长存档供评审参考，
# 视频保留 MP4 有效性与分辨率（≥1080P）校验。
VIDEO_MIN_SHORT_EDGE = 1080
VIDEO_MIN_LONG_EDGE = 1920
CHUNK_BYTES = 1024 * 1024

TOPICS = (
    "职业病危害防护",
    "办公人群肌肉骨骼健康",
    "职场心理健康疏导",
    "新就业形态劳动者健康保护",
    "职业健康法律科普",
    "职场急救",
    "工作场所健康生活方式",
)


@dataclass(frozen=True)
class SavedWork:
    stored_filename: str
    original_filename: str
    file_size: int
    file_sha256: str
    word_count: int | None = None
    duration_seconds: float | None = None
    video_width: int | None = None
    video_height: int | None = None


def private_submission_dir() -> Path:
    root = configured_storage_path(settings.OCCUPATIONAL_SUBMISSION_DIR)
    public_root = configured_storage_path(settings.UPLOAD_DIR)
    if root == public_root or root.is_relative_to(public_root):
        raise RuntimeError("职业健康投稿私有目录不能位于公开上传目录内")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def private_file_path(stored_filename: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(?:docx|mp4)", stored_filename):
        raise HTTPException(status_code=404, detail="文件不存在")
    root = private_submission_dir()
    path = (root / stored_filename).resolve()
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return path


def _docx_word_count(path: Path) -> int:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 200 or sum(item.file_size for item in members) > 40 * 1024 * 1024:
                raise ValueError("Word 文件结构过大")
            names = set(archive.namelist())
            # _rels/.rels 是 OOXML 包的必需关系部件：Word/WPS/Pages 导出的 docx 必有，
            # 缺失时 Word 判定文件损坏（2026-10-08 演示数据踩坑后加固）
            required = {"word/document.xml", "[Content_Types].xml", "_rels/.rels"}
            if not required.issubset(names):
                raise ValueError("不是有效的 Word 文档")
            if any(name.lower().endswith("vbaproject.bin") for name in names):
                raise ValueError("不接受含宏的 Word 文档")
            content_types = archive.read("[Content_Types].xml")
            if b"wordprocessingml.document.main+xml" not in content_types or b"macroEnabled" in content_types:
                raise ValueError("不是无宏 DOCX 文档")
            document_info = archive.getinfo("word/document.xml")
            if document_info.file_size > 8 * 1024 * 1024:
                raise ValueError("Word 正文过大")
            document_xml = archive.read("word/document.xml")
            if b"<!DOCTYPE" in document_xml.upper() or b"<!ENTITY" in document_xml.upper():
                raise ValueError("Word 文档包含不支持的 XML 实体")
            root = ElementTree.fromstring(document_xml)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, ElementTree.ParseError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=f"无法解析 DOCX：{exc}") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        paragraphs.append("".join(node.text or "" for node in paragraph.iter(f"{namespace}t")))
    text = "\n".join(paragraphs)
    # 中文按字、英文/数字连续串按一个词计算；不计标点及空白。
    return len(re.findall(r"[\u3400-\u9fff]|[A-Za-z0-9]+", text))


def _probe_mp4(path: Path) -> tuple[float, int, int]:
    with path.open("rb") as source:
        header = source.read(16)
    if len(header) < 12 or header[4:8] != b"ftyp" or header[8:12] == b"qt  ":
        raise HTTPException(status_code=400, detail="文件不是有效的 MP4")
    try:
        result = subprocess.run(
            [
                settings.FFPROBE_BIN,
                "-v", "error",
                "-show_entries", "format=duration,format_name:stream=codec_type,width,height",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="视频校验服务暂不可用") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail="无法解析 MP4 视频")
    try:
        data = json.loads(result.stdout)
        format_data = data["format"]
        duration = float(format_data["duration"])
        video_stream = next(s for s in data["streams"] if s.get("codec_type") == "video")
        width, height = int(video_stream["width"]), int(video_stream["height"])
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise HTTPException(status_code=400, detail="MP4 缺少有效视频信息") from exc
    if "mp4" not in str(format_data.get("format_name") or "") or not math.isfinite(duration):
        raise HTTPException(status_code=400, detail="文件不是有效的 MP4")
    short_edge, long_edge = sorted((width, height))
    if short_edge < VIDEO_MIN_SHORT_EDGE or long_edge < VIDEO_MIN_LONG_EDGE:
        raise HTTPException(status_code=400, detail="视频分辨率需至少为 1080P")
    return duration, width, height


async def save_and_validate_work(file: UploadFile, work_type: str) -> SavedWork:
    suffix = ".docx" if work_type == "article" else ".mp4"
    maximum = ARTICLE_MAX_BYTES if work_type == "article" else VIDEO_MAX_BYTES
    original_filename = Path((file.filename or "").replace("\\", "/")).name
    original_filename = "".join(ch for ch in original_filename if ch.isprintable())[:255]
    if not original_filename.lower().endswith(suffix):
        raise HTTPException(status_code=400, detail=f"请上传 {suffix} 格式文件")

    root = private_submission_dir()
    temporary = tempfile.NamedTemporaryFile(prefix=".incoming-", dir=root, delete=False)
    temporary_path = Path(temporary.name)
    stored_filename = f"{uuid.uuid4().hex}{suffix}"
    final_path = root / stored_filename
    size = 0
    digest = hashlib.sha256()
    try:
        with temporary:
            while chunk := await file.read(CHUNK_BYTES):
                if size == 0:
                    if work_type == "article" and not chunk.startswith(b"PK\x03\x04"):
                        raise HTTPException(status_code=400, detail="文件不是有效的 DOCX")
                    if work_type == "video" and (
                        len(chunk) < 12 or chunk[4:8] != b"ftyp" or chunk[8:12] == b"qt  "
                    ):
                        raise HTTPException(status_code=400, detail="文件不是有效的 MP4")
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(status_code=413, detail=f"文件不能超过 {maximum // 1024 // 1024} MiB")
                digest.update(chunk)
                await run_in_threadpool(temporary.write, chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="文件内容为空")
        if work_type == "article":
            word_count = await run_in_threadpool(_docx_word_count, temporary_path)
            media = SavedWork(
                stored_filename, original_filename, size, digest.hexdigest(), word_count=word_count
            )
        else:
            duration, width, height = await run_in_threadpool(_probe_mp4, temporary_path)
            media = SavedWork(
                stored_filename, original_filename, size, digest.hexdigest(),
                duration_seconds=duration, video_width=width, video_height=height,
            )
        temporary_path.replace(final_path)
        return media
    except Exception:
        temporary_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


async def hash_uploaded_work(file: UploadFile, work_type: str) -> str:
    """计算幂等重试文件的哈希；重放不会再复制或校验已入库的文件。"""
    maximum = ARTICLE_MAX_BYTES if work_type == "article" else VIDEO_MAX_BYTES
    size = 0
    digest = hashlib.sha256()
    while chunk := await file.read(CHUNK_BYTES):
        size += len(chunk)
        if size > maximum:
            raise HTTPException(status_code=413, detail=f"文件不能超过 {maximum // 1024 // 1024} MiB")
        digest.update(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="文件内容为空")
    return digest.hexdigest()
