"""Checks for generated narration audio: the file's own bytes decide its type, never its name.

Only WAV (PCM) and MP3 are stored. A WAV file must have a RIFF size that matches
the file, a PCM ``fmt`` chunk and a ``data`` chunk, and its duration is computed
from them. An MP3 must start with an ID3 tag or an MPEG audio frame header, and
its next frame must follow where the first one says. Anything else (HTML error
pages, JSON, truncated files) is rejected.
"""
from dataclasses import dataclass
from pathlib import Path
import struct

MAX_AUDIO_BYTES = 50 * 1024 * 1024
# Longest narration one file may hold (a scene or a script read in one request).
MAX_AUDIO_SECONDS = 30 * 60


@dataclass(frozen=True)
class AudioInfo:
    content_type: str
    extension: str
    duration: float | None
    sample_rate: int | None = None
    channels: int | None = None


def pcm_to_wav(pcm: bytes, *, sample_rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw little-endian PCM samples in a WAV container."""
    if not pcm or len(pcm) % (channels * sample_width):
        raise ValueError("PCM data is empty or not whole samples")
    byte_rate = sample_rate * channels * sample_width
    header = (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
              + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate,
                                      channels * sample_width, sample_width * 8)
              + b"data" + struct.pack("<I", len(pcm)))
    return header + pcm


def _wav(data: bytes) -> AudioInfo:
    if len(data) < 44 or struct.unpack_from("<I", data, 4)[0] + 8 != len(data):
        raise ValueError("WAV size does not match its header")
    offset, fmt, frames = 12, None, None
    while offset + 8 <= len(data):
        chunk, size = data[offset:offset + 4], struct.unpack_from("<I", data, offset + 4)[0]
        body = offset + 8
        if body + size > len(data):
            raise ValueError("Truncated WAV chunk")
        if chunk == b"fmt " and size >= 16:
            fmt = struct.unpack_from("<HHIIHH", data, body)
        elif chunk == b"data":
            frames = size
        offset = body + size + (size & 1)
    if fmt is None or frames is None:
        raise ValueError("WAV needs fmt and data chunks")
    audio_format, channels, rate, byte_rate, block_align, bits = fmt
    if audio_format != 1 or not 1 <= channels <= 2 or not 8000 <= rate <= 192000 or bits not in (8, 16, 24, 32):
        raise ValueError("Only 8–192 kHz mono or stereo PCM WAV is accepted")
    if block_align != channels * bits // 8 or byte_rate != rate * block_align or not frames or frames % block_align:
        raise ValueError("Inconsistent WAV format")
    duration = frames / byte_rate
    if duration > MAX_AUDIO_SECONDS:
        raise ValueError("Audio is too long")
    return AudioInfo("audio/wav", "wav", round(duration, 3), rate, channels)


# MPEG-1 Layer III bitrates (kbit/s) and sample rates; enough to find the second frame.
_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _frame_length(header: bytes) -> int | None:
    if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return None
    version, layer = (header[1] >> 3) & 3, (header[1] >> 1) & 3
    bitrate_index, rate_index, padding = header[2] >> 4, (header[2] >> 2) & 3, (header[2] >> 1) & 1
    if version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
        return None  # only Layer III with a real bitrate
    rate = _RATES[version][rate_index]
    bitrate = (_BITRATES if version == 3 else _BITRATES_V2)[bitrate_index] * 1000
    return (144 if version == 3 else 72) * bitrate // rate + padding


def _mp3(data: bytes) -> AudioInfo:
    offset = 0
    if data[:3] == b"ID3":
        if len(data) < 10:
            raise ValueError("Truncated ID3 tag")
        size = data[6:10]
        if any(byte & 0x80 for byte in size):
            raise ValueError("Invalid ID3 size")
        offset = 10 + (size[0] << 21 | size[1] << 14 | size[2] << 7 | size[3])
    length = _frame_length(data[offset:offset + 4])
    if length is None:
        raise ValueError("No MPEG audio frame")
    following = data[offset + length:offset + length + 4]
    if following and _frame_length(following) is None:
        raise ValueError("MPEG frames are not consecutive")
    return AudioInfo("audio/mpeg", "mp3", None)


def inspect_audio(path: Path, *, max_bytes: int = MAX_AUDIO_BYTES) -> AudioInfo:
    """The audio type and duration from the file's bytes; raises ``ValueError`` for anything else."""
    size = path.stat().st_size
    if not 0 < size <= max_bytes:
        raise ValueError("Audio file is empty or too large")
    data = path.read_bytes()
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return _wav(data)
    if data[:3] == b"ID3" or data[:1] == b"\xff":
        return _mp3(data)
    raise ValueError("Not a WAV or MP3 file")
