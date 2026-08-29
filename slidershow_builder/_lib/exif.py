import json
import logging
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime
from typing import Optional

from .optional_deps import MissingOptionalDependency
from .paths import CACHE_DIR

logger = logging.getLogger(__name__)

try:
    import piexif
except ImportError:
    piexif = None

CACHE_FILE = CACHE_DIR / "exif_cache.json"
"""EXIF results keyed by path|size|mtime. `collect` then repeated `build` reruns over the
same files would otherwise re-read (and re-decode) every image's EXIF each time."""

cache: dict[str, list] = {}


@contextmanager
def read_exif_cache(enabled: bool):
    """Context manager for the persistent `read_exif()` cache, mirroring `filename_cache`.

    Usage:
        with read_exif_cache(m.env.read_exif_cache):
            ...
    """
    global cache
    if enabled and CACHE_FILE.is_file():
        try:
            cache = json.loads(CACHE_FILE.read_text())
        except (OSError, ValueError):
            pass
    try:
        yield
    finally:
        if enabled:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(json.dumps(cache))


def dms_to_dd(dms, ref: str) -> float:
    degrees = dms[0][0] / dms[0][1]
    minutes = dms[1][0] / dms[1][1]
    seconds = dms[2][0] / dms[2][1]
    dd = degrees + minutes / 60 + seconds / 3600
    if ref in ("S", "W"):
        dd = -dd
    return dd


def read_exif(path: Path) -> tuple[Optional[str], Optional[tuple[float, float]], Optional[datetime]]:
    if piexif is None:
        raise MissingOptionalDependency("Reading EXIF", "build")

    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        stat = None
    if stat is not None:
        entry = cache.get(key)
        if entry and entry[0] == stat.st_size and entry[1] == int(stat.st_mtime):
            model, gps, dt_iso = entry[2], entry[3], entry[4]
            return model, (tuple(gps) if gps else None), (datetime.fromisoformat(dt_iso) if dt_iso else None)

    model, gps, dt = _read_exif_uncached(path)
    if stat is not None:
        cache[key] = [stat.st_size, int(stat.st_mtime), model, list(gps) if gps else None,
                      dt.isoformat() if dt else None]
    return model, gps, dt


def _read_exif_uncached(path: Path) -> tuple[Optional[str], Optional[tuple[float, float]], Optional[datetime]]:
    try:
        exif_data = piexif.load(str(path))
    except Exception as e:
        # piexif reads JPEG/TIFF only, so every HEIC raises here — and a truncated JPEG can
        # too. Neither is a reason to abort a presentation of thousands of files.
        logger.debug("no EXIF from %s: %s", path, e)
        return None, None, None

    # model
    model = None
    make = exif_data["0th"].get(piexif.ImageIFD.Make, b"").decode(errors="ignore").strip()
    device = exif_data["0th"].get(piexif.ImageIFD.Model, b"").decode(errors="ignore").strip()
    if make and device:
        model = f"{make} {device}"

    # datetime
    dt = None
    raw_dt = exif_data["Exif"].get(piexif.ExifIFD.DateTimeOriginal, b"").decode(errors="ignore").strip()
    if raw_dt:
        try:
            dt = datetime.strptime(raw_dt, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            pass

    # gps
    gps = None
    gps_data = exif_data.get("GPS", {})
    try:
        lat = dms_to_dd(gps_data[piexif.GPSIFD.GPSLatitude], gps_data[piexif.GPSIFD.GPSLatitudeRef].decode())
        lon = dms_to_dd(gps_data[piexif.GPSIFD.GPSLongitude], gps_data[piexif.GPSIFD.GPSLongitudeRef].decode())
        gps = (lat, lon)
    except (KeyError, ZeroDivisionError):
        pass

    return model, gps, dt