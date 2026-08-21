from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fastapi import UploadFile

from src.backend.repositories.evidence_repository import create_evidence_record
from src.backend.repositories.mock_db_repository import PROJECT_ROOT


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def _detect_image(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def store_customer_evidence(
    *,
    upload: UploadFile,
    customer_id: str,
    request_id: str,
    customer_description: str,
) -> dict:
    customer_description = customer_description.strip()
    if len(customer_description) < 5:
        raise ValueError("请至少用 5 个字描述图片中的问题")
    if len(customer_description) > 1000:
        raise ValueError("问题描述不能超过 1000 个字")
    target_dir = PROJECT_ROOT / "data" / "uploads" / customer_id / request_id
    target_dir.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    final_path: Path | None = None
    file_size = 0
    header = b""

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=target_dir,
            prefix=".upload-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := upload.file.read(UPLOAD_CHUNK_BYTES):
                file_size += len(chunk)
                if file_size > MAX_UPLOAD_BYTES:
                    raise ValueError("图片不能超过 10MB")
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                temp_file.write(chunk)

        if file_size == 0:
            raise ValueError("上传文件不能为空")
        detected = _detect_image(header)
        if detected is None:
            raise ValueError("仅支持真实的 JPG、PNG 或 WebP 图片")
        media_type, extension = detected
        final_path = target_dir / f"{uuid4().hex}{extension}"
        temp_path.replace(final_path)
        temp_path = None

        original_name = Path(upload.filename or "evidence").name[:255]
        relative_path = final_path.relative_to(PROJECT_ROOT).as_posix()
        return create_evidence_record(
            request_id=request_id,
            original_filename=original_name,
            media_type=media_type,
            storage_path=relative_path,
            file_size_bytes=file_size,
            customer_description=customer_description,
        )
    except Exception:
        if final_path is not None and final_path.exists():
            final_path.unlink()
        raise
    finally:
        upload.file.close()
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
