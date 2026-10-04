"""Download image artifacts returned by a ChatGPT Web conversation turn."""

from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
import uuid

from curl_cffi.requests import AsyncSession

from ..config import defaults
from ..core.types import TokenBundle
from .conduit import ConduitHandshake


_ASSET_ID = re.compile(r"file[-_][A-Za-z0-9_-]+\Z")


def asset_id_from_pointer(pointer: str) -> str:
    """Extract a validated file ID from a server asset pointer."""
    if not isinstance(pointer, str):
        raise ValueError("Image asset pointer must be text")
    parsed = urlsplit(pointer)
    if parsed.scheme not in {"sediment", "file-service"}:
        raise ValueError("Unsupported image asset pointer scheme")
    asset_id = (parsed.netloc or parsed.path.lstrip("/")).split("/", 1)[0]
    if not _ASSET_ID.fullmatch(asset_id):
        raise ValueError("Invalid image asset ID")
    return asset_id


def default_image_dir() -> Path:
    return Path.home() / "Pictures" / "gpt-images"


def _image_extension(mime_type: str) -> str:
    if not isinstance(mime_type, str) or not mime_type.lower().startswith("image/"):
        raise ValueError("Image artifact has no valid image MIME type")
    extension = mimetypes.guess_extension(mime_type.split(";", 1)[0].lower())
    if extension == ".jpe":
        return ".jpg"
    if not extension:
        raise ValueError("Unsupported image MIME type")
    return extension


async def download_image_asset(
    session: AsyncSession,
    bundle: TokenBundle,
    asset: dict[str, object],
    *,
    user_agent: str | None = None,
    output_dir: Path | str | None = None,
) -> Path:
    """Fetch one generated image using the current ChatGPT Web credentials."""
    asset_id = asset_id_from_pointer(str(asset.get("asset_pointer") or ""))
    mime_type = str(asset.get("mime_type") or "")
    extension = _image_extension(mime_type)
    directory = Path(output_dir).expanduser() if output_dir is not None else default_image_dir()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"gpt-image-{asset_id}{extension}"
    if destination.is_file():
        return destination

    path = f"/backend-api/files/download/{quote(asset_id, safe='')}"
    headers = ConduitHandshake.build_integrity_headers(
        bundle,
        str(uuid.uuid4()),
        str(uuid.uuid4()),
        user_agent=user_agent,
        target_path=path,
    )
    headers["Accept"] = "*/*"
    headers.pop("Content-Type", None)
    response = await session.get(
        f"{defaults.CHATGPT_BASE_URL}{path}",
        headers=headers,
        timeout=60.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Image download returned HTTP {response.status_code}")

    content_type = response.headers.get("content-type", response.headers.get("Content-Type", ""))
    if not content_type.lower().startswith(("image/", "application/octet-stream")):
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("Image download returned neither image data nor a download descriptor") from exc
        download_url = payload.get("download_url") if isinstance(payload, dict) else None
        if not isinstance(download_url, str):
            raise RuntimeError("Image download descriptor did not contain a download URL")
        parsed_url = urlsplit(download_url)
        expected_host = urlsplit(defaults.CHATGPT_BASE_URL).hostname
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname != expected_host
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.path != "/backend-api/estuary/content"
        ):
            raise RuntimeError("Image download descriptor contained an unexpected URL")

        content_headers = dict(headers)
        content_headers["x-openai-target-path"] = parsed_url.path
        content_headers["x-openai-target-route"] = parsed_url.path
        content_response = await session.get(
            download_url,
            headers=content_headers,
            timeout=60.0,
        )
        if content_response.status_code != 200:
            raise RuntimeError(f"Image content download returned HTTP {content_response.status_code}")
        content_type = content_response.headers.get(
            "content-type", content_response.headers.get("Content-Type", "")
        )
        response = content_response

    if content_type and not content_type.lower().startswith(("image/", "application/octet-stream")):
        raise RuntimeError("Image content download returned a non-image response")
    body = response.content
    if not body:
        raise RuntimeError("Image download returned an empty body")

    try:
        with destination.open("xb") as image_file:
            image_file.write(body)
    except FileExistsError:
        # A concurrent turn already saved this immutable asset ID.
        return destination
    except OSError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return destination
