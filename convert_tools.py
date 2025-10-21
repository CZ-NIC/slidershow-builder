import logging
import shlex
import subprocess
from hashlib import blake2b
from pathlib import Path
from subprocess import run

logger = logging.getLogger(__name__)


def file_meta_key(p: Path):
    stat = p.stat()
    meta = f"{p.name}|{stat.st_size}|{int(stat.st_mtime)}"
    return blake2b(meta.encode(), digest_size=3).hexdigest()


def heic_to_jpg(orig: Path, target: Path):
    logger.info(f"Converting to a compatible format in cache {orig} → {target}")
    run(["convert", orig, target])


def ffmpeg_video(orig: Path, target: Path):
    logger.info(f"Converting to a compatible format in cache {orig} → {target}")
    ffmpeg_command = [
        "ffmpeg",
        "-i",
        orig,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "22",
        "-c:a",
        "aac",
        "-strict",
        "experimental",
        "-v",
        "warning",
        "-stats",
        target,
    ]
    logger.debug("ffmpeg command: %s", shlex.join([str(c) for c in ffmpeg_command]))
    run(ffmpeg_command)


def is_hevc(path: Path) -> bool:
    """Check whether the video is HEVC/H.265."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        codec = result.stdout.strip().lower()
        return codec in ("hevc", "h265")
    except subprocess.CalledProcessError:
        return False
