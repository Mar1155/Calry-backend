import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

UPLOAD_DIR = Path("app/static/uploads")


def _extension_for(file: UploadFile) -> str:
    ext = Path(file.filename).suffix if file.filename else ""
    if ext:
        return ext
    content_type = file.content_type or ""
    if "image" in content_type:
        return ".jpg"
    if "audio" in content_type:
        return ".mp3"
    return ".bin"


def _s3_public_url(key: str) -> str:
    if settings.S3_PUBLIC_URL_BASE:
        return f"{settings.S3_PUBLIC_URL_BASE.rstrip('/')}/{key}"
    return f"https://{settings.S3_BUCKET}.s3.{settings.S3_REGION}.amazonaws.com/{key}"


async def save_upload(file: UploadFile, user_id: int) -> dict[str, str]:
    ext = _extension_for(file)
    key = f"uploads/{user_id}/{uuid.uuid4()}{ext}"

    if settings.STORAGE_BACKEND == "s3":
        return await _save_upload_s3(file, key)

    return await _save_upload_local(file, key)


async def _save_upload_local(file: UploadFile, key: str) -> dict[str, str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = Path(key).name
    dest_path = UPLOAD_DIR / filename

    def write_file() -> None:
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    await run_in_threadpool(write_file)
    return {"url": f"/static/uploads/{filename}", "storage": "local"}


async def _save_upload_s3(file: UploadFile, key: str) -> dict[str, str]:
    if not settings.S3_BUCKET:
        raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")

    def upload() -> None:
        import boto3

        client = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )
        extra_args = {"ContentType": file.content_type or "application/octet-stream"}
        if settings.S3_PUBLIC_READ:
            extra_args["ACL"] = "public-read"
        client.upload_fileobj(file.file, settings.S3_BUCKET, key, ExtraArgs=extra_args)

    await run_in_threadpool(upload)
    return {"url": _s3_public_url(key), "storage": "s3", "key": key}
