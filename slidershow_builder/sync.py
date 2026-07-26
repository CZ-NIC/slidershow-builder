"""Tree sync — the core of upload.edvard.cz's previews.php, ported. See PLAN.md."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .cache import MirrorLayout
from .media import DEFAULT_TOOLS, Tools, fix_mtime as _fix_mtime, probe, thumbnail, to_h264, to_jpeg

logger = logging.getLogger(__name__)


@dataclass
class PreviewTarget:
    dir: Path
    size: int = 320
    quality: int = 75


@dataclass
class FallbackTarget:
    dir: Path
    jpeg_quality: int = 92
    crf: int = 20


@dataclass
class SyncReport:
    created: dict[str, list[Path]] = field(default_factory=lambda: {"preview": [], "fallback": []})
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    removed: list[Path] = field(default_factory=list)
    failed: list[tuple[Path, str]] = field(default_factory=list)
    mtime_fixed: list[tuple[Path, datetime]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(
            self.created["preview"] or self.created["fallback"]
            or self.moved or self.removed or self.failed or self.mtime_fixed
        )

    def to_json(self) -> dict:
        return {
            "created": {k: [str(p) for p in v] for k, v in self.created.items()},
            "moved": [[str(a), str(b)] for a, b in self.moved],
            "removed": [str(p) for p in self.removed],
            "failed": [[str(p), reason] for p, reason in self.failed],
            "mtime_fixed": [[str(p), dt.isoformat()] for p, dt in self.mtime_fixed],
        }


def _prune_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    dirs = sorted((d for d in root.rglob("*") if d.is_dir()), key=lambda p: len(p.parts), reverse=True)
    for d in dirs:
        try:
            d.rmdir()
        except OSError:
            pass  # not empty


def _find_moved(dst: Path, layout_dir: Path, claimed: set) -> Optional[Path]:
    """A source file was renamed/moved: look for a cache entry with the same basename
    elsewhere under the cache dir that no other file has claimed yet."""
    if not layout_dir.exists():
        return None
    for candidate in layout_dir.rglob(dst.name):
        if candidate.is_file() and candidate != dst and candidate not in claimed:
            return candidate
    return None


def sync_tree(
    source: Path,
    *,
    previews: Optional[PreviewTarget] = None,
    fallbacks: Optional[FallbackTarget] = None,
    fix_mtime: bool = False,
    detect_moves: bool = True,
    prune_orphans: bool = True,
    tools: Tools = DEFAULT_TOOLS,
    on_event: Optional[Callable[[str], None]] = None,
) -> SyncReport:
    report = SyncReport()
    log = on_event or (lambda msg: logger.info(msg))

    preview_layout = MirrorLayout(previews.dir, source) if previews else None
    fallback_layout = MirrorLayout(fallbacks.dir, source) if fallbacks else None
    output_dirs = [d.dir.resolve() for d in (previews, fallbacks) if d is not None]

    seen_previews: set[Path] = set()
    seen_fallbacks: set[Path] = set()

    for path in source.rglob("*"):
        if not path.is_file():
            continue
        if any(d == path.resolve() or d in path.resolve().parents for d in output_dirs):
            continue  # preview/fallback dir nested inside source: never sync our own output
        info = probe(path, tools=tools)
        if info.kind == "other":
            continue

        if fix_mtime:
            fixed = _fix_mtime(path, tools=tools)
            if fixed is not None:
                report.mtime_fixed.append((path, fixed))
                log(f"mtime fixed: {path} -> {fixed}")

        if preview_layout is not None:
            dst = preview_layout.path_for(path, ".webp")
            seen_previews.add(dst)
            if not dst.exists():
                moved_from = _find_moved(dst, preview_layout.cache_dir, seen_previews) if detect_moves else None
                if moved_from is not None:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    moved_from.rename(dst)
                    report.moved.append((moved_from, dst))
                    log(f"preview moved: {moved_from} -> {dst}")
                elif thumbnail(path, dst, size=previews.size, quality=previews.quality, tools=tools):
                    report.created["preview"].append(dst)
                    log(f"preview created: {dst}")
                else:
                    report.failed.append((path, "preview generation failed"))

        if fallback_layout is not None and not info.browser_compatible:
            suffix = ".jpg" if info.kind == "photo" else ".mp4"
            dst = fallback_layout.path_for(path, suffix)
            seen_fallbacks.add(dst)
            if not dst.exists():
                ok = (
                    to_jpeg(path, dst, quality=fallbacks.jpeg_quality)
                    if info.kind == "photo"
                    else to_h264(path, dst, crf=fallbacks.crf, tools=tools)
                )
                if ok:
                    report.created["fallback"].append(dst)
                    log(f"fallback created: {dst}")
                else:
                    report.failed.append((path, "fallback generation failed"))

    if prune_orphans:
        for layout, seen, kind in (
            (preview_layout, seen_previews, "preview"),
            (fallback_layout, seen_fallbacks, "fallback"),
        ):
            if layout is None or not layout.cache_dir.exists():
                continue
            for existing in layout.cache_dir.rglob("*"):
                if existing.is_file() and existing not in seen:
                    existing.unlink()
                    report.removed.append(existing)
                    log(f"{kind} orphan removed: {existing}")
            _prune_empty_dirs(layout.cache_dir)

    return report
