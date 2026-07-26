"""Media probing/conversion primitives shared by the sheet-driven build path
and the tree-sync path (`slidershow-builder previews`).

See PLAN.md for the rationale behind this module.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover - optional at runtime, required for HEIC
    pass

logger = logging.getLogger(__name__)

PHOTO_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".avif", ".webp",
    ".heic", ".heif", ".bmp", ".tiff", ".tif", ".jp2", ".psd",
}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".hevc", ".m4v", ".webm"}

COMPATIBLE_VCODECS = {"h264", "vp9", "vp8", "av1"}
COMPATIBLE_ACODECS = {"aac", "mp3", "opus", "vorbis"}


@dataclass
class Tools:
    """Configurable paths to external binaries (needed e.g. on shared hosting)."""

    ffmpeg: Path = Path("ffmpeg")
    ffprobe: Path = Path("ffprobe")


DEFAULT_TOOLS = Tools()


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    kind: Literal["photo", "video", "other"]
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    capture_time: Optional[datetime] = None
    browser_compatible: bool = False


def _kind_of(path: Path) -> Literal["photo", "video", "other"]:
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in PHOTO_SUFFIXES:
        return "photo"
    return "other"


def _ffprobe_streams(path: Path, tools: Tools) -> tuple[Optional[str], Optional[str]]:
    try:
        result = subprocess.run(
            [
                str(tools.ffprobe), "-v", "error",
                "-show_entries", "stream=codec_type,codec_name",
                "-of", "default=noprint_wrappers=1", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None, None
    vcodec = acodec = None
    codec_type = None
    for line in result.stdout.splitlines():
        if line.startswith("codec_type="):
            codec_type = line.split("=", 1)[1].strip()
        elif line.startswith("codec_name="):
            name = line.split("=", 1)[1].strip()
            if codec_type == "video" and vcodec is None:
                vcodec = name
            elif codec_type == "audio" and acodec is None:
                acodec = name
    return vcodec, acodec


def _ffprobe_creation_time(path: Path, tools: Tools) -> Optional[datetime]:
    try:
        result = subprocess.run(
            [
                str(tools.ffprobe), "-v", "error",
                "-show_entries", "format_tags=creation_time",
                "-of", "default=nokey=1:noprint_wrappers=1", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _photo_capture_time(path: Path) -> Optional[datetime]:
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
    except Exception:
        return None
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def capture_time(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> Optional[datetime]:
    kind = _kind_of(path)
    if kind == "video":
        return _ffprobe_creation_time(path, tools)
    if kind == "photo":
        return _photo_capture_time(path)
    return None


def probe(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> MediaInfo:
    kind = _kind_of(path)
    if kind == "video":
        vcodec, acodec = _ffprobe_streams(path, tools)
        compatible = vcodec in COMPATIBLE_VCODECS and (acodec is None or acodec in COMPATIBLE_ACODECS)
        return MediaInfo(path, "video", vcodec, acodec, _ffprobe_creation_time(path, tools), compatible)
    if kind == "photo":
        return MediaInfo(path, "photo", None, None, _photo_capture_time(path), True)
    return MediaInfo(path, "other", None, None, None, True)


def thumbnail(src: Path, dst: Path, *, size: int = 320, quality: int = 75, tools: Tools = DEFAULT_TOOLS) -> bool:
    """Write a WebP thumbnail (long edge `size` px) for a photo or video frame."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    kind = _kind_of(src)
    if kind == "photo":
        return _photo_thumbnail(src, dst, size, quality)
    if kind == "video":
        return _video_thumbnail(src, dst, size, tools)
    return False


def _photo_thumbnail(src: Path, dst: Path, size: int, quality: int) -> bool:
    try:
        with Image.open(src) as im:
            im.seek(0)  # first frame of animated GIF / multi-frame HEIC
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size))
            im.convert("RGB").save(dst, "WEBP", quality=quality)
        return True
    except Exception as e:
        logger.warning("thumbnail failed for %s: %s", src, e)
        return False


def _video_thumbnail(src: Path, dst: Path, size: int, tools: Tools) -> bool:
    scale = f"scale='min({size},iw)':'min({size},ih)':force_original_aspect_ratio=decrease"
    for seek in ("1", "0"):  # videos shorter than 1s fall back to the very first frame
        cmd = [
            str(tools.ffmpeg), "-y", "-ss", seek, "-i", str(src),
            "-frames:v", "1", "-vf", scale, str(dst),
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
        if dst.exists() and dst.stat().st_size > 0:
            return True
    return False


def to_jpeg(src: Path, dst: Path, *, quality: int = 92) -> bool:
    """HEIC/HEIF -> full-size JPEG, orientation preserved."""
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            im.convert("RGB").save(dst, "JPEG", quality=quality)
        return True
    except Exception as e:
        logger.warning("to_jpeg failed for %s: %s", src, e)
        return False


def to_h264(src: Path, dst: Path, *, crf: int = 20, preset: str = "veryfast", tools: Tools = DEFAULT_TOOLS) -> bool:
    """Transcode to a browser-compatible H.264/AAC mp4 with +faststart."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(tools.ffmpeg), "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-c:a", "aac", "-movflags", "+faststart", str(dst),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("to_h264 failed for %s: %s", src, e)
        return False


def fix_mtime(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> Optional[datetime]:
    """Set mtime from capture time if it differs by more than 1s. Returns the new time, or None if unchanged."""
    captured = capture_time(path, tools=tools)
    if captured is None:
        return None
    current = datetime.fromtimestamp(path.stat().st_mtime)
    if abs((current - captured).total_seconds()) <= 1:
        return None
    ts = captured.timestamp()
    os.utime(path, (ts, ts))
    return captured
