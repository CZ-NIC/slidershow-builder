from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Callable

from .convert_tools import ffmpeg_video, file_meta_key, heic_to_jpg, is_hevc

IMAGE_SUFFIXES = ".jpg", ".jpeg", ".jxl", ".png", ".gif", ".avif", ".webp", ".heic"

logger = logging.getLogger(__name__)


@dataclass
class Convert:
    """Auto-convert for browser-compatible formats.

    Creates a cached copies with compatible JPG and MP4.
    """

    enable: bool = False
    """The cache will be used for needy media."""

    autogenerate: bool = True
    """If .enable, generate all the needy media to the cache. """

    check_mp4_for_hevc: bool = True

    cache_dir: Path = Path("/tmp")

    def __post_init__(self):
        if self.enable and not self.cache_dir.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_cached_path(self, p: Path, suffix: str):
        """suffix with dot"""
        return self.cache_dir / (p.name + f".{file_meta_key(p)}{suffix}")

    def get_converted(self, path: Path, suffix: str, method: Callable):
        cached = self.get_cached_path(path, suffix)
        exists = cached.exists()
        if self.autogenerate and not exists:
            method(path, cached)
        if not self.autogenerate and not exists:
            return path
        return cached

    def run(self, path: Path):
        suff = path.suffix.lower()
        if self.enable:
            if not path.exists():
                logger.warning(f"Filename {path} does not exist")
            else:
                match suff:
                    case ".heic":
                        path = self.get_converted(path, ".jpg", heic_to_jpg)
                    case ".hevc":
                        path = self.get_converted(path, ".mp4", ffmpeg_video)
                    case ".mp4":
                        if self.check_mp4_for_hevc and is_hevc(path):
                            path = self.get_converted(path, ".mp4", ffmpeg_video)

        return path
