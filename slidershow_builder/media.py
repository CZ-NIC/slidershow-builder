"""Media probing/conversion primitives shared by the sheet-driven build path
and the tree-sync path (`slidershow-builder previews`).

See PLAN.md for the rationale behind this module.
"""

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from ._lib.optional_deps import MissingOptionalDependency

try:
    from PIL import Image, ImageFile, ImageOps

    ImageFile.LOAD_TRUNCATED_IMAGES = True
    _PILLOW_AVAILABLE = True
except ImportError:
    _PILLOW_AVAILABLE = False

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # HEIC just won't open; only matters if a .heic file is touched
    pass

logger = logging.getLogger(__name__)


def _require_pillow():
    if not _PILLOW_AVAILABLE:
        raise MissingOptionalDependency("Photo/video processing", "media")

PHOTO_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".avif", ".webp",
    ".heic", ".heif", ".bmp", ".tiff", ".tif", ".jp2", ".psd",
}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".3gp", ".hevc", ".m4v", ".webm",
                  ".mpg", ".mpeg"}

#: Photo formats no mainstream browser decodes — the only ones that get a JPEG fallback.
#: (`.tif`/`.psd`/`.jp2` are unplayable too, but they do not appear in practice and
#: converting them wholesale would be a surprise; add them here if that ever changes.)
INCOMPATIBLE_PHOTO_SUFFIXES = {".heic", ".heif"}

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


def kind_of(path: Path) -> Literal["photo", "video", "other"]:
    """Media kind by file extension only — no I/O, cheap enough for a whole-tree walk."""
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in PHOTO_SUFFIXES:
        return "photo"
    return "other"


_kind_of = kind_of  # backwards-compatible alias


def _ffprobe_streams(path: Path, tools: Tools) -> tuple[Optional[str], Optional[str]]:
    # JSON, not the flat format: ffprobe emits the requested fields in its own order
    # (`codec_name` before `codec_type`), so line-wise parsing cannot pair them up.
    try:
        result = subprocess.run(
            [
                str(tools.ffprobe), "-v", "error",
                "-show_entries", "stream=codec_type,codec_name",
                "-of", "json", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None, None
    vcodec = acodec = None
    for stream in streams:
        if stream.get("codec_type") == "video" and vcodec is None:
            vcodec = stream.get("codec_name")
        elif stream.get("codec_type") == "audio" and acodec is None:
            acodec = stream.get("codec_name")
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
    # Container timestamps are UTC ("2026-07-20T09:29:50.000000Z"); EXIF ones (below) are
    # local and naive. Everything this module returns is naive local time, so convert.
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            utc = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return utc.astimezone().replace(tzinfo=None)
    try:  # no trailing Z: an offset may or may not be present
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _photo_capture_time(path: Path) -> Optional[datetime]:
    _require_pillow()
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
    kind = kind_of(path)
    if kind == "video":
        return _ffprobe_creation_time(path, tools)
    if kind == "photo":
        return _photo_capture_time(path)
    return None


def is_compatible_video(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> bool:
    """Does every mainstream browser play this natively? Costs one ffprobe call."""
    vcodec, acodec = _ffprobe_streams(path, tools)
    return vcodec in COMPATIBLE_VCODECS and (acodec is None or acodec in COMPATIBLE_ACODECS)


def probe(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> MediaInfo:
    kind = kind_of(path)
    if kind == "video":
        vcodec, acodec = _ffprobe_streams(path, tools)
        compatible = vcodec in COMPATIBLE_VCODECS and (acodec is None or acodec in COMPATIBLE_ACODECS)
        return MediaInfo(path, "video", vcodec, acodec, _ffprobe_creation_time(path, tools), compatible)
    if kind == "photo":
        compatible = path.suffix.lower() not in INCOMPATIBLE_PHOTO_SUFFIXES
        return MediaInfo(path, "photo", None, None, _photo_capture_time(path), compatible)
    return MediaInfo(path, "other", None, None, None, True)


def thumbnail(src: Path, dst: Path, *, size: int = 320, quality: int = 75, tools: Tools = DEFAULT_TOOLS) -> bool:
    """Write a WebP thumbnail (long edge `size` px) for a photo or video frame."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    kind = kind_of(src)
    if kind == "photo":
        return _photo_thumbnail(src, dst, size, quality)
    if kind == "video":
        return _video_thumbnail(src, dst, size, quality, tools)
    return False


def _photo_thumbnail(src: Path, dst: Path, size: int, quality: int) -> bool:
    _require_pillow()
    try:
        with Image.open(src) as im:
            # No seek() needed: an open animated GIF sits on frame 0, a multi-frame HEIC on
            # its primary image, and a PSD on its flattened composite (whose "frames" are
            # layers numbered from 1, so seek(0) would raise there).
            im = ImageOps.exif_transpose(im)
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            # no exif=/icc_profile= passed → metadata is stripped, as ImageMagick's stripImage() did
            im.convert("RGB").save(dst, "WEBP", quality=quality)
        return True
    except Exception as e:
        logger.warning("thumbnail failed for %s: %s", src, e)
        return False


def _video_thumbnail(src: Path, dst: Path, size: int, quality: int, tools: Tools) -> bool:
    scale = (f"scale=w={size}:h={size}:force_original_aspect_ratio=decrease"
             ":force_divisible_by=2")
    for seek in ("1", "0"):  # videos shorter than 1s have no frame at -ss 1
        cmd = [
            str(tools.ffmpeg), "-hide_banner", "-loglevel", "error",
            "-ss", seek, "-i", str(src),
            "-frames:v", "1", "-vf", scale, "-quality", str(quality),
            "-y", str(dst),
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning("thumbnail (-ss %s) failed for %s: %s", seek, src,
                           getattr(e, "stderr", b"")[-500:] or e)
            dst.unlink(missing_ok=True)
            continue
        if dst.exists() and dst.stat().st_size > 0:
            return True
        dst.unlink(missing_ok=True)
    return False


def to_jpeg(src: Path, dst: Path, *, quality: int = 92) -> bool:
    """HEIC/HEIF -> full-size JPEG, orientation preserved."""
    _require_pillow()
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
        str(tools.ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(src),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart", "-y", str(dst),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("to_h264 failed for %s: %s", src, e)
        return False


def fix_mtime(path: Path, *, tools: Tools = DEFAULT_TOOLS) -> Optional[datetime]:
    """Set mtime from capture time if it differs by more than 1s. Returns the new time, or None if unchanged.

    A symlink gets **its own** mtime set, never its target's. A tree of symlinks (what
    `collect` builds) points at somebody else's originals: the capture time is *read*
    through the link, but nothing is ever written past it. Everything that walks a tree
    goes through this function, so that guarantee holds for `previews --fix-mtime` too.
    """
    captured = capture_time(path, tools=tools)
    if captured is None:
        return None
    link = path.is_symlink()
    current = datetime.fromtimestamp((os.lstat(path) if link else path.stat()).st_mtime)
    if abs((current - captured).total_seconds()) <= 1:
        return None
    ts = captured.timestamp()
    os.utime(path, (ts, ts), follow_symlinks=not link)
    return captured
