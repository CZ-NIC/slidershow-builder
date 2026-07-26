from dataclasses import dataclass, field
import logging
from pathlib import Path

from .. import media
from ..cache import ContentHashLayout

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

    cache_dir: Path = Path("/tmp")

    heic: bool = True
    """Generate JPG from HEIC."""
    hevc: bool = True
    """Generate MP4 from HEVC."""
    hevc_in_mp4: bool = True
    """ Check for HEVC codec in MP4 video files."""

    def __post_init__(self):
        if self.enable and not self.cache_dir.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._layout = ContentHashLayout(self.cache_dir)
        self._tools = media.Tools()

    def get_cached_path(self, p: Path, suffix: str) -> Path:
        """suffix with dot"""
        return self._layout.path_for(p, suffix)

    def get_converted(self, path: Path, suffix: str, method) -> Path:
        cached = self.get_cached_path(path, suffix)
        exists = cached.exists()
        if self.autogenerate and not exists:
            method(path, cached)
        if not self.autogenerate and not exists:
            return path
        return cached

    def run(self, path: Path) -> Path:
        suff = path.suffix.lower()
        if self.enable:
            if not path.exists():
                logger.warning(f"Filename {path} does not exist")
            else:
                match suff:
                    case ".heic":
                        if self.heic:
                            path = self.get_converted(path, ".jpg", lambda s, d: media.to_jpeg(s, d))
                    case ".hevc":
                        if self.hevc:
                            path = self.get_converted(
                                path, ".mp4", lambda s, d: media.to_h264(s, d, tools=self._tools)
                            )
                    case ".mp4":
                        if self.hevc and self.hevc_in_mp4:
                            info = media.probe(path, tools=self._tools)
                            if info.vcodec in ("hevc", "h265"):
                                path = self.get_converted(
                                    path, ".mp4", lambda s, d: media.to_h264(s, d, tools=self._tools)
                                )
        return path
