import logging
import mimetypes
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import settings

logger = logging.getLogger("app.services.storage")

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


def _content_type_for(file: UploadFile, key: str) -> str:
    content_type = file.content_type
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed_type, _ = mimetypes.guess_type(key)
    return guessed_type or "application/octet-stream"


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

    try:
        await run_in_threadpool(write_file)
    except OSError:
        logger.exception("local upload write failed key=%s", key)
        raise
    return {"url": f"/static/uploads/{filename}", "storage": "local"}


async def _save_upload_s3(file: UploadFile, key: str) -> dict[str, str]:
    if not settings.S3_BUCKET:
        logger.error("s3 upload attempted without S3_BUCKET configured key=%s", key)
        raise RuntimeError("S3_BUCKET is required when STORAGE_BACKEND=s3")

    def upload() -> None:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )
        extra_args = {"ContentType": _content_type_for(file, key)}
        if settings.S3_PUBLIC_READ:
            extra_args["ACL"] = "public-read"
        try:
            client.upload_fileobj(
                file.file,
                settings.S3_BUCKET,
                key,
                ExtraArgs=extra_args,
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            code = error.get("Code", "Unknown")
            message = error.get("Message", str(exc))
            logger.error("s3 upload failed key=%s code=%s message=%s", key, code, message)
            if settings.S3_PUBLIC_READ and code in {
                "AccessControlListNotSupported",
                "AccessDenied",
                "InvalidRequest",
            }:
                raise RuntimeError(
                    "S3 upload could not apply public-read ACL. Disable S3_PUBLIC_READ "
                    "and add a bucket policy that allows public s3:GetObject on uploads/*, "
                    "or allow object ACLs for this bucket."
                ) from exc
            raise RuntimeError(f"S3 upload failed ({code}): {message}") from exc

    await run_in_threadpool(upload)
    logger.info("s3 upload succeeded key=%s", key)
    return {"url": _s3_public_url(key), "storage": "s3", "key": key}


def storage_key_from_url(url: str) -> str | None:
    """Resolve only URLs belonging to configured Calry storage."""
    if not url:
        return None
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if path.startswith("/static/uploads/"):
        return f"local:{Path(path).name}"

    if settings.STORAGE_BACKEND != "s3" or not settings.S3_BUCKET:
        return None
    if settings.S3_PUBLIC_URL_BASE:
        base = urlparse(settings.S3_PUBLIC_URL_BASE)
        prefix = base.path.rstrip("/") + "/"
        if parsed.netloc == base.netloc and path.startswith(prefix):
            return path[len(prefix) :].lstrip("/")
    expected_hosts = {
        f"{settings.S3_BUCKET}.s3.{settings.S3_REGION}.amazonaws.com",
        f"{settings.S3_BUCKET}.s3.amazonaws.com",
    }
    if parsed.netloc in expected_hosts and path.startswith("/uploads/"):
        return path.lstrip("/")
    if settings.S3_ENDPOINT_URL:
        endpoint = urlparse(settings.S3_ENDPOINT_URL)
        prefix = f"/{settings.S3_BUCKET}/"
        if parsed.netloc == endpoint.netloc and path.startswith(prefix):
            return path[len(prefix) :]
    return None


async def delete_storage_object(storage_key: str) -> bool:
    """Delete one owned object. Missing objects count as successful absence."""
    if storage_key.startswith("local:"):
        filename = storage_key.removeprefix("local:")
        if filename != Path(filename).name:
            raise ValueError("Invalid local storage key.")
        path = UPLOAD_DIR / filename

        def unlink() -> bool:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

        return await run_in_threadpool(unlink)

    if settings.STORAGE_BACKEND != "s3" or not settings.S3_BUCKET:
        raise RuntimeError("S3 storage is not configured for this object.")
    if not storage_key.startswith("uploads/") or ".." in Path(storage_key).parts:
        raise ValueError("Invalid S3 storage key.")

    def delete_s3() -> bool:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client(
            "s3",
            region_name=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )
        try:
            client.head_object(Bucket=settings.S3_BUCKET, Key=storage_key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise
        client.delete_object(Bucket=settings.S3_BUCKET, Key=storage_key)
        return True

    return await run_in_threadpool(delete_s3)
