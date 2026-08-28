import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from .optional_deps import MissingOptionalDependency

logger = logging.getLogger(__name__)

try:
    import piexif
except ImportError:
    piexif = None


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