"""Gather originals scattered over several disks into one folder of symlinks.

The input is a set of file *names* (from `people.csv`, i.e. Google Photos, which knows
nothing about where the files actually live) and/or a set of calendar days; the output is
a folder you can hand straight to slidershow. Symlinks, so nothing is duplicated —
which is also why nothing here ever writes into the originals: they belong to whatever
backup tree they came from. The only thing stamped is the *symlink's own* mtime
(`media.fix_mtime` handles that distinction), so the folder sorts by capture time.

Files no browser can display (HEIC/HEIF, exotic video codecs) are the one thing a plain
symlink cannot solve — see `Collect.incompatible` for the three ways out.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional

from ._lib.paths import CACHE_DIR
from .media import (
    DEFAULT_TOOLS,
    INCOMPATIBLE_PHOTO_SUFFIXES,
    Tools,
    capture_time,
    is_compatible_video,
    kind_of,
    to_h264,
    to_jpeg,
)

logger = logging.getLogger(__name__)

DATE_CACHE = CACHE_DIR / "capture_time.json"
"""Capture times keyed by path|size|mtime. A --whole-day run reads EXIF (and ffprobes every
video) of the whole search tree; without this, every re-run pays that again."""

Incompatible = Literal["replace", "fallback", "link"]


@dataclass
class CollectReport:
    linked: list[tuple[Path, Path]] = field(default_factory=list)
    """(original, new symlink)"""
    converted: list[tuple[Path, Path]] = field(default_factory=list)
    """(original, browser-playable copy written into the destination) — `replace` mode only"""
    kept: int = 0
    """Links that were already there from an earlier run."""
    missing: list[str] = field(default_factory=list)
    """Names from the index with no original anywhere in the search dirs."""
    ambiguous: dict[str, list[Path]] = field(default_factory=dict)
    """Name -> every candidate found; the first one was used."""
    failed: list[tuple[Path, str]] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "linked": [[str(a), str(b)] for a, b in self.linked],
            "converted": [[str(a), str(b)] for a, b in self.converted],
            "kept": self.kept,
            "missing": self.missing,
            "ambiguous": {k: [str(p) for p in v] for k, v in self.ambiguous.items()},
            "failed": [[str(p), reason] for p, reason in self.failed],
        }


def parse_days(specs: Iterable[str]) -> set[str]:
    """`"2026-08-05"` or `"2026-08-05:2026-08-07"` -> the set of ISO days covered."""
    days: set[str] = set()
    for spec in specs:
        start_str, _, end_str = str(spec).partition(":")
        start = date.fromisoformat(start_str.strip())
        end = date.fromisoformat(end_str.strip()) if end_str.strip() else start
        if end < start:
            raise ValueError(f"Date range ends before it starts: {spec}")
        days.update((start + timedelta(days=n)).isoformat() for n in range((end - start).days + 1))
    return days


class _DateIndex:
    """`file -> the day it was taken`, cached across runs, mtime fallback for the undated."""

    def __init__(self, tools: Tools, use_cache: bool = True):
        self.tools = tools
        self.use_cache = use_cache
        self.cache: dict[str, list] = {}
        self.dirty = False
        if use_cache and DATE_CACHE.is_file():
            try:
                loaded = json.loads(DATE_CACHE.read_text())
                if isinstance(loaded, dict):
                    self.cache = loaded
            except (OSError, ValueError):
                pass

    def taken(self, path: Path) -> datetime:
        stat = path.stat()
        key = str(path)
        entry = self.cache.get(key)
        if not (entry and entry[0] == stat.st_size and entry[1] == int(stat.st_mtime)):
            captured = capture_time(path, tools=self.tools)
            entry = [stat.st_size, int(stat.st_mtime), captured.isoformat() if captured else None]
            self.cache[key] = entry
            self.dirty = True
        if entry[2]:
            return datetime.fromisoformat(entry[2])
        return datetime.fromtimestamp(stat.st_mtime)

    def day(self, path: Path) -> str:
        return self.taken(path).date().isoformat()

    def save(self) -> None:
        if self.use_cache and self.dirty:
            DATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            DATE_CACHE.write_text(json.dumps(self.cache))


def _index(search_dirs: Iterable[Path], excluded: set[Path], dates: Optional[_DateIndex],
           log: Callable[[str], None]) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    by_name: dict[str, list[Path]] = {}
    by_day: dict[str, list[Path]] = {}
    for base in search_dirs:
        if not base.is_dir():
            log(f"! not a directory, skipped: {base}")
            continue
        for path in base.rglob("*"):
            if not path.is_file() or kind_of(path) == "other":
                continue
            # A destination nested in a search dir would otherwise be re-collected into
            # itself, linking symlinks to symlinks on every run.
            if any(d == path.parent or d in path.parents for d in excluded):
                continue
            by_name.setdefault(path.name.lower(), []).append(path)
            if dates is not None:
                by_day.setdefault(dates.day(path), []).append(path)
    return by_name, by_day


def _free_name(dest: Path, name: str, source: Path) -> tuple[Path, bool]:
    """A free path in `dest` for `name`; the flag says this exact link is already there."""
    candidate = dest / name
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 1
    while candidate.exists() or candidate.is_symlink():
        if candidate.is_symlink() and candidate.resolve() == source.resolve():
            return candidate, True
        candidate = dest / f"{stem}_{i}{suffix}"
        i += 1
    return candidate, False


def _convert_into(source: Path, dst: Path, make: Callable[[Path, Path], bool]) -> bool:
    """Write via `<dst>.part.<ext>` and rename, so an interrupted run leaves no half file
    that slidershow would happily try to display."""
    part = dst.with_name(dst.name + ".part" + dst.suffix)
    try:
        ok = make(source, part) and part.is_file() and part.stat().st_size > 0
    except Exception as e:
        logger.warning("conversion of %s crashed: %s", source, e)
        ok = False
    if ok:
        part.replace(dst)
        return True
    part.unlink(missing_ok=True)
    return False


def _replacement_suffix(source: Path, tools: Tools) -> Optional[str]:
    """`.jpg`/`.mp4` if no browser can show this file as it is, else None."""
    kind = kind_of(source)
    if kind == "photo":
        return ".jpg" if source.suffix.lower() in INCOMPATIBLE_PHOTO_SUFFIXES else None
    if kind == "video":
        return None if is_compatible_video(source, tools=tools) else ".mp4"
    return None


def collect(
    dest: Path,
    *,
    search_dirs: list[Path],
    filenames: Iterable[str] = (),
    days: Iterable[str] = (),
    whole_day_of: Iterable[str] = (),
    incompatible: Incompatible = "replace",
    set_mtime: bool = True,
    dry_run: bool = False,
    date_cache: bool = True,
    tools: Tools = DEFAULT_TOOLS,
    excluded: Iterable[Path] = (),
    on_event: Optional[Callable[[str], None]] = None,
) -> CollectReport:
    """Symlink every matching original into `dest`.

    `filenames` are matched by base name (case-insensitively); `days` and `whole_day_of`
    are ISO days matched against capture time. `whole_day_of` exists so that a person's
    tagged photos can drag in the rest of that day's outing — it is a separate argument
    only so the report can tell the two apart.
    """
    report = CollectReport()
    log = on_event or logger.info
    dest = dest.expanduser()

    wanted_days = set(days) | set(whole_day_of)
    dates = _DateIndex(tools, date_cache) if wanted_days else None
    excluded_dirs = {p.resolve() for p in (*excluded, dest) if p.exists() or p == dest}
    by_name, by_day = _index(search_dirs, excluded_dirs, dates, log)

    # resolved original -> why it was picked, for the report and for stable de-duplication
    sources: dict[Path, str] = {}
    for filename in sorted(set(filenames)):
        candidates = by_name.get(filename.lower(), [])
        if not candidates:
            report.missing.append(filename)
            continue
        if len(candidates) > 1:
            report.ambiguous[filename] = candidates
            log(f"! {filename}: {len(candidates)} candidates, using the first")
        sources.setdefault(candidates[0].resolve(), "match")
    for day in sorted(wanted_days):
        reason = "whole-day" if day in set(whole_day_of) else "date-range"
        for path in by_day.get(day, []):
            sources.setdefault(path.resolve(), reason)
    if dates is not None:
        dates.save()

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for source in sorted(sources):
        replacement = _replacement_suffix(source, tools) if incompatible == "replace" else None
        if replacement:
            # `IMG_0004.heic.jpg`, the suffix appended rather than substituted — same
            # convention as the preview/fallback cache (`cache.MirrorLayout`). It keeps the
            # name derived from the source alone, so a re-run recognises its own output
            # instead of converting again under a `_1` name, and `a.heic`/`a.jpg` collected
            # side by side cannot collide.
            target = dest / (source.name + replacement)
            if dry_run:
                log(f"{source} => {target} (converted)")
                continue
            if target.exists():
                report.kept += 1
                continue
            make = ((lambda s, d: to_jpeg(s, d)) if replacement == ".jpg"
                    else (lambda s, d: to_h264(s, d, tools=tools)))
            if _convert_into(source, target, make):
                report.converted.append((source, target))
                log(f"+ {target.name} ← {source}")
            else:
                report.failed.append((source, "conversion failed"))
                log(f"✗ {source}")
                continue
        else:
            link, exists = _free_name(dest, source.name, source)
            if dry_run:
                log(f"{source} -> {link}")
                continue
            if exists:
                report.kept += 1
            else:
                link.symlink_to(source)
                report.linked.append((source, link))
            target = link
        if set_mtime:
            stamp = (dates.taken(source) if dates is not None
                     else capture_time(source, tools=tools)
                     or datetime.fromtimestamp(source.stat().st_mtime))
            # follow_symlinks=False: the link carries the date, the original is not touched
            os.utime(target, (stamp.timestamp(), stamp.timestamp()),
                     follow_symlinks=not target.is_symlink())
    return report
