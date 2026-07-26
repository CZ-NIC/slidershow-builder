"""Cache path addressing strategies.

`Convert` (sheet path) and `sync_tree` (tree path) do the same conversions,
they just address the cache differently — that's the one difference that
warrants this abstraction. See PLAN.md.
"""

from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Protocol


class CacheLayout(Protocol):
    def path_for(self, path: Path, suffix: str) -> Path:
        """`suffix` includes the leading dot."""
        ...


def file_meta_key(p: Path) -> str:
    stat = p.stat()
    meta = f"{p.name}|{stat.st_size}|{int(stat.st_mtime)}"
    return blake2b(meta.encode(), digest_size=3).hexdigest()


@dataclass
class ContentHashLayout:
    """Today's behaviour: `<name>.<blake2b(name|size|mtime)><suffix>`, flat dir."""

    cache_dir: Path

    def path_for(self, path: Path, suffix: str) -> Path:
        return self.cache_dir / (path.name + f".{file_meta_key(path)}{suffix}")


@dataclass
class MirrorLayout:
    """`cache_dir/<rel-to-source_root><suffix>` — mirrors the source tree structure.

    The suffix is *appended*, not substituted: `a/b.heic` -> `cache/a/b.heic.webp`.
    That keeps the original extension visible, lets a template derive the cache path
    from the source name alone, and stops `b.jpg` and `b.png` from colliding.
    """

    cache_dir: Path
    source_root: Path

    def path_for(self, path: Path, suffix: str) -> Path:
        rel = path.relative_to(self.source_root) if path.is_absolute() else path
        return self.cache_dir / rel.parent / (rel.name + suffix)
