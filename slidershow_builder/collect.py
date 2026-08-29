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

from tqdm import tqdm  # TODO switch to mininterface's own progress bar once it has one

from . import people as _people
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
from .people import Row

logger = logging.getLogger(__name__)

DATE_CACHE = CACHE_DIR / "capture_time.json"
"""Capture times keyed by path|size|mtime. A --whole-day run reads EXIF (and ffprobes every
video) of the whole search tree; without this, every re-run pays that again."""

Incompatible = Literal["replace", "fallback", "link"]
PeopleMode = Literal["union", "intersection"]

ResolveAmbiguous = Callable[[dict[str, list[Path]]], dict[str, Optional[Path]]]
"""Given every filename that a date match couldn't settle (each mapped to its candidates),
return a chosen `Path` per filename the caller wants to decide. A filename absent from the
result falls back to the first candidate; a filename mapped to `None` is dropped (goes into
`CollectReport.missing`) rather than collected at all."""


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


class DateIndex:
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

    def _entry(self, path: Path) -> list:
        stat = path.stat()
        key = str(path)
        entry = self.cache.get(key)
        if not (entry and entry[0] == stat.st_size and entry[1] == int(stat.st_mtime)):
            captured = capture_time(path, tools=self.tools)
            entry = [stat.st_size, int(stat.st_mtime), captured.isoformat() if captured else None]
            self.cache[key] = entry
            self.dirty = True
        return entry

    def taken(self, path: Path) -> datetime:
        entry = self._entry(path)
        if entry[2]:
            return datetime.fromisoformat(entry[2])
        return datetime.fromtimestamp(path.stat().st_mtime)

    def captured(self, path: Path) -> Optional[datetime]:
        """Like `taken()`, but None rather than an mtime guess when nothing was read from
        EXIF/ffprobe — a bare mtime is when a file was copied, not when it was shot, so it must
        not be trusted to break a filename tie between two unrelated photos."""
        entry = self._entry(path)
        return datetime.fromisoformat(entry[2]) if entry[2] else None

    def day(self, path: Path) -> str:
        return self.taken(path).date().isoformat()

    def group_key(self, path: Path, unit: Literal["day", "week", "month", "year"]) -> str:
        """`day()` generalized to any calendar unit — backs `build --dir --group-by`, so a
        folder of media can be split into sections without a spreadsheet's SECTION rows."""
        dt = self.taken(path)
        if unit == "day":
            return dt.date().isoformat()
        if unit == "week":
            iso_year, iso_week, _ = dt.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        if unit == "month":
            return f"{dt.year}-{dt.month:02d}"
        return str(dt.year)

    def save(self) -> None:
        if self.use_cache and self.dirty:
            DATE_CACHE.parent.mkdir(parents=True, exist_ok=True)
            DATE_CACHE.write_text(json.dumps(self.cache))


def _index(search_dirs: Iterable[Path], excluded: set[Path], dates: Optional[DateIndex],
           index_days: bool, log: Callable[[str], None],
           ) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    by_name: dict[str, list[Path]] = {}
    by_day: dict[str, list[Path]] = {}
    pbar = tqdm(desc="indexing", unit="file", disable=not index_days)
    with pbar:
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
                if index_days:
                    pbar.set_postfix_str(path.name)
                    by_day.setdefault(dates.day(path), []).append(path)
                    pbar.update()
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


def _pick_by_date(
    candidates: list[Path], hint: Optional[datetime], tolerance: timedelta, dates: DateIndex,
    pbar: Optional[tqdm] = None,
) -> Optional[Path]:
    """The one candidate whose own capture time sits closest to `hint`, within `tolerance` — or
    None if there is no hint or nothing is close enough. A camera's filename counter can repeat
    across years (`DSC09253.JPG` shot twice, a year apart), so a bare filename match is not
    enough; the index's tagged date breaks the tie. The tolerance exists because that tagged
    date usually came from a different pipeline (e.g. Google Photos) than the file's own EXIF
    and the two can disagree by a few hours.

    Several candidates tied for closest is not itself ambiguous — the same photo is routinely
    backed up under more than one path (a yearly archive, a curated subfolder, an old backup
    disk) — so we just pick one of them rather than giving up."""
    if hint is None:
        return None
    scored = []
    for path in candidates:
        if pbar is not None:
            pbar.set_postfix_str(path.name)
        taken = dates.captured(path)
        if taken is None:
            continue
        diff = abs((taken.replace(tzinfo=None) - hint.replace(tzinfo=None)).total_seconds())
        if diff <= tolerance.total_seconds():
            scored.append((diff, path))
    if not scored:
        return None
    return min(scored, key=lambda pair: pair[0])[1]


@dataclass
class ResolvedPeople:
    """Output of `resolve_people`: source paths plus the same missing/ambiguous
    diagnostics `CollectReport` carries, with no destination/symlink concerns baked in."""

    paths: list[Path] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[Path]] = field(default_factory=dict)
    dates: Optional[DateIndex] = None
    """The capture-time cache built while resolving, if any (whole_day/dates were used, or a
    filename matched more than one file on disk) — reused by `collect()` so mtime-stamping does
    not pay for the same ffprobe/EXIF read twice."""


def resolve_people(
    index_rows: set[Row],
    names: Iterable[str],
    search_dirs: Iterable[Path],
    *,
    excluded: Iterable[Path] = (),
    whole_day: bool = False,
    dates: Iterable[str] = (),
    mode: PeopleMode = "union",
    taken_hint: dict[str, datetime] = {},
    date_tolerance: timedelta = timedelta(hours=12),
    resolve_ambiguous: Optional[ResolveAmbiguous] = None,
    tools: Tools = DEFAULT_TOOLS,
    date_cache: bool = True,
    on_event: Optional[Callable[[str], None]] = None,
) -> ResolvedPeople:
    """Names + explicit calendar days -> resolved source paths. No writing, no `dest` —
    the piece `collect` and `build --people` both need before they go their separate ways
    (symlinking vs. rendering straight into HTML).

    `names` are looked up in `index_rows` (`people.read_index()`'s output). `dates` are ISO
    days wanted regardless of who is on the photo (`--date-ranges`). `mode` picks "at least
    one of `names`" (union — `people.files_and_days`'s own behaviour) or "all of `names`
    together" (intersection, computed per-name since the index otherwise collapses several
    names into one combined set). `whole_day` additionally pulls in everything taken on a
    day any selected photo was taken on.

    A filename with more than one candidate on disk is resolved by `taken_hint` (the index's
    tagged capture time for that name, if any) within `date_tolerance`; what that still can't
    settle goes to `resolve_ambiguous` — called once with every such filename, so a caller can
    offer them all in a single batch rather than one dialog per file — or, absent a callback,
    to the first candidate found (the old behavior).
    """
    names = set(names)
    log = on_event or logger.info
    result = ResolvedPeople()

    filenames: set[str] = set()
    person_days: set[str] = set()
    if names:
        if mode == "union":
            filenames, person_days = _people.files_and_days(index_rows, names)
        else:
            by_name = _people.files_by_name(index_rows, names)
            filenames = set.intersection(*by_name.values()) if by_name else set()
            person_days = _people.days_for_files(index_rows, filenames)

    wanted_days = set(dates) | (person_days if whole_day else set())
    # A `DateIndex` is cheap to have around (just a JSON cache load) and disambiguating
    # `filenames` needs one; only actually walking every file's date is expensive, and that
    # stays gated on `wanted_days` alone, same as before.
    date_index = DateIndex(tools, date_cache) if (wanted_days or filenames) else None
    excluded_dirs = {p.resolve() for p in excluded}
    by_name_idx, by_day_idx = _index(search_dirs, excluded_dirs, date_index, bool(wanted_days), log)

    sources: dict[Path, None] = {}
    ambiguous: dict[str, list[Path]] = {}
    for filename in sorted(filenames):
        candidates = by_name_idx.get(filename.lower(), [])
        if not candidates:
            result.missing.append(filename)
        elif len(candidates) == 1:
            sources.setdefault(candidates[0].resolve())
        else:
            result.ambiguous[filename] = candidates
            ambiguous[filename] = candidates

    unresolved: dict[str, list[Path]] = {}
    # One bar for the whole phase — a ffprobe call per candidate can take up to
    # FFPROBE_TIMEOUT, so without it a long resolve looks like a hang rather than progress.
    with tqdm(ambiguous.items(), desc="disambiguating", unit="name",
              disable=not ambiguous) as pbar:
        for filename, candidates in pbar:
            pbar.set_postfix_str(filename)
            chosen = _pick_by_date(candidates, taken_hint.get(filename), date_tolerance, date_index, pbar)
            if chosen is not None:
                sources.setdefault(chosen.resolve())
            else:
                unresolved[filename] = candidates

    if unresolved:
        resolved_choice = resolve_ambiguous(unresolved) if resolve_ambiguous else {}
        for filename, candidates in unresolved.items():
            if filename in resolved_choice:
                chosen = resolved_choice[filename]
                if chosen is None:
                    log(f"- {filename}: dropped by the user, {len(candidates)} candidates")
                    result.missing.append(filename)
                    continue
                log(f"? {filename}: {len(candidates)} candidates, resolved by the user")
            else:
                chosen = candidates[0]
                log(f"! {filename}: {len(candidates)} candidates, no date match, using the first")
            sources.setdefault(chosen.resolve())

    for day in sorted(wanted_days):
        for path in by_day_idx.get(day, []):
            sources.setdefault(path.resolve())
    if date_index is not None:
        date_index.save()

    result.paths = sorted(sources)
    result.dates = date_index
    return result


def collect(
    dest: Path,
    *,
    search_dirs: list[Path],
    names: Iterable[str] = (),
    index_rows: set[Row] = frozenset(),
    days: Iterable[str] = (),
    whole_day: bool = False,
    mode: PeopleMode = "union",
    taken_hint: dict[str, datetime] = {},
    date_tolerance: timedelta = timedelta(hours=12),
    resolve_ambiguous: Optional[ResolveAmbiguous] = None,
    incompatible: Incompatible = "replace",
    set_mtime: bool = True,
    dry_run: bool = False,
    date_cache: bool = True,
    tools: Tools = DEFAULT_TOOLS,
    excluded: Iterable[Path] = (),
    on_event: Optional[Callable[[str], None]] = None,
) -> CollectReport:
    """Symlink every matching original into `dest`.

    `names` are looked up in `index_rows` and matched by base name (case-insensitively);
    `days` and (when `whole_day`) the days the named people were photographed on are matched
    against capture time. Selection itself is `resolve_people`'s job — this just writes it
    (or converts it) into `dest`.
    """
    report = CollectReport()
    log = on_event or logger.info
    dest = dest.expanduser()

    excluded_dirs = {p.resolve() for p in (*excluded, dest) if p.exists() or p == dest}
    resolved = resolve_people(
        index_rows, names, search_dirs, excluded=excluded_dirs, whole_day=whole_day,
        dates=days, mode=mode, taken_hint=taken_hint, date_tolerance=date_tolerance,
        resolve_ambiguous=resolve_ambiguous, tools=tools, date_cache=date_cache, on_event=log,
    )
    report.missing = resolved.missing
    report.ambiguous = resolved.ambiguous
    dates = resolved.dates

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for source in (pbar := tqdm(resolved.paths, desc="collecting", unit="file")):
        pbar.set_postfix_str(source.name)
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
