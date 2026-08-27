"""Validated, canonical and durable user image storage."""

import io
import os
import secrets
import tempfile
import time
import warnings
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from database.models import Workspace


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SOURCE_DIMENSION = 8192
MAX_SOURCE_PIXELS = 20_000_000
MAX_STORED_DIMENSION = 2048
ORPHAN_GRACE_SECONDS = 24 * 60 * 60
UPLOADS_ROOT = Path("webapp/uploads")

_FORMAT_EXTENSION = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
_FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_CLAIMED_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


class InvalidImageUpload(ValueError):
    """Raised when an upload does not satisfy the image storage contract."""


def _open_image(content: bytes) -> Image.Image:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
        return Image.open(io.BytesIO(content))
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise InvalidImageUpload("Изображение имеет небезопасно большое разрешение") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise InvalidImageUpload("Файл не является корректным изображением") from None


def canonicalize_image_upload(
    content: bytes,
    *,
    filename: str,
    content_type: Optional[str],
) -> tuple[bytes, str]:
    """Validate actual bytes and strip metadata by re-encoding the image."""
    if not content:
        raise InvalidImageUpload("Файл изображения пуст")
    if len(content) > MAX_UPLOAD_BYTES:
        raise InvalidImageUpload("Размер файла не должен превышать 5 МБ")

    claimed_extension = Path(filename or "").suffix.lower()
    claimed_format = _CLAIMED_FORMATS.get(claimed_extension)
    if claimed_format is None:
        raise InvalidImageUpload("Поддерживаются только форматы PNG, JPG, WEBP")

    image = _open_image(content)
    actual_format = (image.format or "").upper()
    if actual_format not in _FORMAT_EXTENSION:
        image.close()
        raise InvalidImageUpload("Поддерживаются только форматы PNG, JPG, WEBP")
    if claimed_format != actual_format:
        image.close()
        raise InvalidImageUpload("Расширение файла не соответствует содержимому изображения")

    declared_mime = (content_type or "").split(";", 1)[0].strip().lower()
    if declared_mime not in ("", "application/octet-stream", _FORMAT_MIME[actual_format]):
        image.close()
        raise InvalidImageUpload("Тип файла не соответствует содержимому изображения")

    width, height = image.size
    if (
        width < 1
        or height < 1
        or width > MAX_SOURCE_DIMENSION
        or height > MAX_SOURCE_DIMENSION
        or width * height > MAX_SOURCE_PIXELS
    ):
        image.close()
        raise InvalidImageUpload("Изображение имеет недопустимое разрешение")
    if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
        image.close()
        raise InvalidImageUpload("Анимированные изображения не поддерживаются")

    processed_image = image
    converted_image = None
    try:
        processed_image = ImageOps.exif_transpose(image)
        processed_image.thumbnail(
            (MAX_STORED_DIMENSION, MAX_STORED_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        has_alpha = processed_image.mode in ("RGBA", "LA") or "transparency" in processed_image.info
        converted_image = processed_image.convert(
            "RGBA" if has_alpha and actual_format != "JPEG" else "RGB"
        )

        output = io.BytesIO()
        if actual_format == "JPEG":
            converted_image.save(output, format="JPEG", quality=90, optimize=True)
        elif actual_format == "WEBP":
            converted_image.save(output, format="WEBP", quality=90, method=6)
        else:
            converted_image.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
    except (OSError, ValueError):
        raise InvalidImageUpload("Не удалось безопасно обработать изображение") from None
    finally:
        if converted_image is not None:
            converted_image.close()
        if processed_image is not image:
            processed_image.close()
        image.close()

    if len(encoded) > MAX_UPLOAD_BYTES:
        raise InvalidImageUpload("Обработанное изображение превышает лимит 5 МБ")
    return encoded, _FORMAT_EXTENSION[actual_format]


def save_image_upload(
    content: bytes,
    *,
    filename: str,
    content_type: Optional[str],
    category: str,
    owner_user_id: int,
) -> str:
    if category not in ("avatars", "workspaces"):
        raise ValueError("Unsupported upload category")
    encoded, extension = canonicalize_image_upload(
        content,
        filename=filename,
        content_type=content_type,
    )
    prefix = "avatar" if category == "avatars" else "logo"
    unique_filename = f"{prefix}_{owner_user_id}_{int(time.time())}_{secrets.token_hex(4)}{extension}"
    upload_dir = UPLOADS_ROOT / category
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=upload_dir)
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(encoded)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        target_path = upload_dir / unique_filename
        os.replace(temporary_name, target_path)
        target_path.chmod(0o644)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return f"/uploads/{category}/{unique_filename}"


def local_upload_path(url: str, category: str) -> Optional[Path]:
    prefix = f"/uploads/{category}/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return None
    filename = url[len(prefix) :]
    if not filename or filename != os.path.basename(filename):
        return None
    return UPLOADS_ROOT / category / filename


def is_owned_workspace_logo(url: str, owner_user_id: int) -> bool:
    path = local_upload_path(url, "workspaces")
    return bool(
        path
        and path.name.startswith(f"logo_{owner_user_id}_")
        and path.is_file()
    )


def is_owned_avatar(url: str, owner_user_id: int) -> bool:
    path = local_upload_path(url, "avatars")
    return bool(
        path
        and path.name.startswith(f"avatar_{owner_user_id}_")
        and path.is_file()
    )


def delete_local_upload(url: str, category: str, owner_prefix: str = "") -> bool:
    path = local_upload_path(url, category)
    if path is None or (owner_prefix and not path.name.startswith(owner_prefix)):
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


async def delete_workspace_logo_if_unreferenced(session, logo_url: str) -> bool:
    path = local_upload_path(logo_url, "workspaces")
    if path is None:
        return False
    reference = (
        await session.execute(
            select(Workspace.id).where(Workspace.logo_url == logo_url).limit(1)
        )
    ).scalar_one_or_none()
    return False if reference is not None else delete_local_upload(logo_url, "workspaces")


async def cleanup_stale_workspace_logos(
    session,
    *,
    grace_seconds: int = ORPHAN_GRACE_SECONDS,
) -> int:
    upload_dir = UPLOADS_ROOT / "workspaces"
    if not upload_dir.is_dir():
        return 0
    referenced = set(
        (
            await session.execute(
                select(Workspace.logo_url).where(
                    Workspace.logo_url.like("/uploads/workspaces/%")
                )
            )
        ).scalars()
    )
    cutoff = time.time() - max(0, grace_seconds)
    removed = 0
    for path in upload_dir.iterdir():
        if not path.is_file() or not path.name.startswith("logo_"):
            continue
        url = f"/uploads/workspaces/{path.name}"
        try:
            is_stale = path.stat().st_mtime <= cutoff
        except OSError:
            continue
        if url not in referenced and is_stale and delete_local_upload(url, "workspaces"):
            removed += 1
    return removed
