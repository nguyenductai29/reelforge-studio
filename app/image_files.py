"""Downloading and checking generated images, shared by the image worker and the smoke test.

Only PNG, JPEG and WEBP are accepted, recognized by their bytes (never by a
header or a URL), so an SVG or HTML page cannot pass as an image. A file must
also be complete: PNG ends with IEND, JPEG with EOI, and a WEBP's RIFF size
matches the file. Nothing here touches the database.
"""
from dataclasses import dataclass
from pathlib import Path

import httpx

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_DIMENSION = 20000
# Connecting may take 30 s; each read of the stream may take 120 s.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_HEADER_TYPES = {"image/png", "image/jpeg", "image/webp", "application/octet-stream"}


@dataclass(frozen=True)
class ImageInfo:
    content_type: str
    extension: str
    width: int
    height: int


def download_image(url: str, target: Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> int:
    """Stream an image to ``target``; returns its size. Redirects are not followed (the URL was already checked)."""
    size = 0
    with httpx.Client(follow_redirects=False, timeout=DOWNLOAD_TIMEOUT) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0].strip() not in _HEADER_TYPES:
                raise ValueError("Provider did not return a PNG, JPEG or WEBP image")
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Provider image exceeds the size limit")
                    handle.write(chunk)
    return size


def _png(data: bytes) -> ImageInfo:
    if len(data) < 45 or data[12:16] != b"IHDR" or data[-8:-4] != b"IEND":
        raise ValueError("Incomplete PNG image")
    return ImageInfo("image/png", "png", int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _jpeg(data: bytes) -> ImageInfo:
    if len(data) < 16 or data[-2:] != b"\xff\xd9":
        raise ValueError("Incomplete JPEG image")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise ValueError("Malformed JPEG image")
        marker = data[offset + 1]
        if marker == 0xFF:  # fill byte
            offset += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        if length < 2 or offset + 2 + length > len(data):
            raise ValueError("Malformed JPEG image")
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            segment = data[offset + 4:offset + 2 + length]
            if len(segment) < 5:
                raise ValueError("Malformed JPEG image")
            return ImageInfo("image/jpeg", "jpg", int.from_bytes(segment[3:5], "big"),
                             int.from_bytes(segment[1:3], "big"))
        if marker == 0xDA:  # compressed data starts before any frame header
            break
        offset += 2 + length
    raise ValueError("JPEG image has no frame header")


def _webp(data: bytes) -> ImageInfo:
    if len(data) < 30 or int.from_bytes(data[4:8], "little") + 8 != len(data):
        raise ValueError("Incomplete WEBP image")
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
    elif chunk == b"VP8 ":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
    elif chunk == b"VP8L":
        b0, b1, b2, b3 = data[21:25]
        width = 1 + (b0 | (b1 & 0x3F) << 8)
        height = 1 + ((b1 >> 6) | b2 << 2 | (b3 & 0x0F) << 10)
    else:
        raise ValueError("Unsupported WEBP image")
    return ImageInfo("image/webp", "webp", width, height)


def inspect_image(path: Path, *, max_bytes: int = MAX_IMAGE_BYTES) -> ImageInfo:
    """The image's real type and size in pixels; raises ``ValueError`` for anything else."""
    size = path.stat().st_size
    if not 0 < size <= max_bytes:
        raise ValueError("Image is empty or too large")
    data = path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        info = _png(data)
    elif data.startswith(b"\xff\xd8\xff"):
        info = _jpeg(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        info = _webp(data)
    else:
        raise ValueError("Not a PNG, JPEG or WEBP image")
    if not (0 < info.width <= MAX_DIMENSION and 0 < info.height <= MAX_DIMENSION):
        raise ValueError("Image dimensions are invalid")
    return info
