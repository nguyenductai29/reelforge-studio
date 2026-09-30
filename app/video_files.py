"""Downloading and checking provider MP4 files, shared by the video worker and the smoke test.

Nothing here touches the database, so ``app.smoke_test`` can use it without a
configured instance.
"""
from pathlib import Path

import httpx

MAX_VIDEO_BYTES = 100 * 1024 * 1024
# Connecting may take 30 s; each read of the stream may take 120 s.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, read=120.0)
_VIDEO_TYPES = {"video/mp4", "application/octet-stream"}


def download_video(url: str, target: Path, *, max_bytes: int = MAX_VIDEO_BYTES) -> int:
    """Stream an MP4 to ``target``; returns its size. Redirects are not followed (the URL was already checked)."""
    size = 0
    with httpx.Client(follow_redirects=False, timeout=DOWNLOAD_TIMEOUT) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            if response.status_code != 200 or response.headers.get("content-type", "").split(";")[0] not in _VIDEO_TYPES:
                raise ValueError("Provider did not return an MP4 video")
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Provider video exceeds 100 MB")
                    handle.write(chunk)
    return size


def _top_level_boxes(path: Path):
    """``(type, payload_offset, payload_size)`` of each top-level box, or ``None`` if the container is malformed."""
    file_size = path.stat().st_size
    boxes = []
    with path.open("rb") as handle:
        offset = 0
        while offset < file_size:
            if len(boxes) >= 4096 or file_size - offset < 8:
                return None
            header = handle.read(8)
            if len(header) != 8:
                return None
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            header_size = 8
            if box_size == 1:
                large_size = handle.read(8)
                if len(large_size) != 8:
                    return None
                box_size = int.from_bytes(large_size, "big")
                header_size = 16
            elif box_size == 0:
                box_size = file_size - offset
            if box_size < header_size or box_size > file_size - offset:
                return None
            boxes.append((box_type, offset + header_size, box_size - header_size))
            offset += box_size
            handle.seek(offset)
    return boxes


def valid_mp4(path: Path, *, max_bytes: int = MAX_VIDEO_BYTES) -> bool:
    """Reject truncated containers before recording usage (codec checks come later)."""
    try:
        if not 32 <= path.stat().st_size <= max_bytes:
            return False
        boxes = _top_level_boxes(path)
    except OSError:
        return False
    if not boxes or boxes[0][0] != b"ftyp" or boxes[0][2] < 8:
        return False
    return (any(kind == b"moov" and size >= 8 for kind, _, size in boxes)
            and any(kind == b"mdat" and size > 0 for kind, _, size in boxes))


def mp4_duration_seconds(path: Path) -> float | None:
    """The movie duration from the ``mvhd`` box, or ``None`` if it cannot be read."""
    try:
        boxes = _top_level_boxes(path)
        moov = next((box for box in boxes or () if box[0] == b"moov"), None)
        if moov is None:
            return None
        with path.open("rb") as handle:
            handle.seek(moov[1])
            data = handle.read(min(moov[2], 1 << 20))
    except OSError:
        return None
    offset = 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        if size < 8:
            return None
        if data[offset + 4:offset + 8] == b"mvhd":
            body = data[offset + 8:offset + size]
            if len(body) < 20:
                return None
            if body[0] == 1:
                if len(body) < 32:
                    return None
                timescale = int.from_bytes(body[20:24], "big")
                duration = int.from_bytes(body[24:32], "big")
            else:
                timescale = int.from_bytes(body[12:16], "big")
                duration = int.from_bytes(body[16:20], "big")
            return round(duration / timescale, 3) if timescale else None
        offset += size
    return None
