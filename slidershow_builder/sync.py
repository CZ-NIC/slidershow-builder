"""Tree sync — the core of upload.edvard.cz's previews.php, ported. See PLAN.md.

Mirrors a source media tree into a preview cache (and, for formats browsers cannot
decode, a fallback cache). Designed to be run from cron every minute over thousands
of files, so the steady state must be cheap: a file that already has its preview
costs one `stat`, nothing else. In particular no file is ever decoded or probed
just to find out that there is nothing to do.

Conventions shared with the PHP implementation it replaces (a switch-over must not
invalidate the existing cache, and either implementation cleans up after the other):

* `cache/<rel><suffix>` — suffix appended, see `MirrorLayout`
* `<dst>.part.<ext>`    — half-written output; renamed onto `<dst>` only once complete
* `<dst>.fail`          — conversion failed; do not retry until the source changes
* `<fallback_dir>/.compatible-videos.json` — "this video needs no fallback" verdicts,
  keyed by source mtime, so ffprobe does not run on every video every minute
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Literal, Optional

from tqdm import tqdm  # TODO switch to mininterface's own progress bar once it has one

from .cache import MirrorLayout
from .media import (
    DEFAULT_TOOLS,
    INCOMPATIBLE_PHOTO_SUFFIXES,
    Tools,
    fix_mtime as _fix_mtime,
    is_compatible_video,
    kind_of,
    thumbnail,
    to_h264,
    to_jpeg,
)

logger = logging.getLogger(__name__)

STALE_PART_AGE = 3600
"""A `.part` file older than this is a leftover of a crashed/killed run, not work in progress."""

COMPAT_CACHE_NAME = ".compatible-videos.json"

_FALLBACK_ORIGINAL_RE = re.compile(r"\.(jpg|mp4)(\.fail|\.part\.\w+)?$")


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
    kinds: Literal["both", "photo", "video"] = "both"
    """Which incompatible media to convert. Transcoding video is orders of magnitude more
    expensive than converting a photo — hours of CPU and tens of GB for a large tree — so it
    is worth being able to do the cheap half on its own."""


@dataclass
class SyncReport:
    """What the run changed. `created`/`failed`/`mtime_fixed` name **source** files,
    `moved`/`removed` name **cache** files (their source is gone or elsewhere)."""

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


def _walk(base: Path) -> Iterator[Path]:
    """Regular files under `base`. Dotfiles are skipped (hidden *directories* are entered,
    matching the PHP implementation)."""
    if not base.is_dir():
        return
    for path in base.rglob("*"):
        if path.is_file() and not path.name.startswith("."):
            yield path


def _stale(path: Path) -> bool:
    return time.time() - path.stat().st_mtime > STALE_PART_AGE


def _fail_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".fail")


def _part_path(dst: Path) -> Path:
    # `<dst>.part.<ext>`: ffmpeg/Pillow infer the output format from the extension,
    # so the temporary name has to keep it.
    return dst.with_name(dst.name + ".part" + dst.suffix)


def _remove_with_empty_dirs(file: Path, base: Path) -> None:
    file.unlink(missing_ok=True)
    parent = file.parent
    while parent != base and base in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break  # not empty
        parent = parent.parent


def _generate(src: Path, dst: Path, make: Callable[[Path, Path], bool]) -> Optional[bool]:
    """Run `make` into a `.part` file and atomically rename it onto `dst`, so the web
    never serves a half-written file.

    Returns True when created, False when the conversion failed now (a `.fail` marker
    is left behind), None when a previous failure is still current — the caller reports
    only genuine, new failures.
    """
    fail = _fail_path(dst)
    if fail.is_file() and fail.stat().st_mtime >= src.stat().st_mtime:
        return None
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dst)
    try:
        ok = make(src, part) and part.is_file() and part.stat().st_size > 0
    except Exception as e:  # a broken source must never kill the whole cron run
        logger.warning("conversion of %s crashed: %s", src, e)
        ok = False
    if ok:
        part.replace(dst)
        fail.unlink(missing_ok=True)
        return True
    part.unlink(missing_ok=True)
    fail.touch()
    return False


def _sync_previews(
    originals: dict[str, Path],
    target: PreviewTarget,
    layout: MirrorLayout,
    report: SyncReport,
    log: Callable[[str], None],
    *,
    detect_moves: bool,
    prune_orphans: bool,
    fix_mtime: bool,
    tools: Tools,
) -> None:
    root = layout.cache_dir
    existing: dict[str, Path] = {}  # source rel path -> its preview

    for path in _walk(root):
        rel = str(path.relative_to(root))
        if rel.endswith(".part.webp"):
            if _stale(path):
                path.unlink(missing_ok=True)
        elif rel.endswith(".webp.fail"):
            if rel[: -len(".webp.fail")] not in originals:
                _remove_with_empty_dirs(path, root)
        elif rel.endswith(".webp"):
            existing[rel[: -len(".webp")]] = path

    missing = [rel for rel in originals if rel not in existing]
    orphans = {rel: path for rel, path in existing.items() if rel not in originals}

    # A moved/renamed source shows up as one orphaned preview plus one missing preview
    # with the same basename — move the preview instead of re-encoding it. Pure
    # optimization: on any ambiguity we fall through to delete + regenerate.
    if detect_moves and orphans and missing:
        by_basename: dict[str, list[str]] = {}
        for rel in missing:
            by_basename.setdefault(Path(rel).name, []).append(rel)
        for rel, preview in list(orphans.items()):
            candidates = by_basename.get(Path(rel).name, [])
            if len(candidates) != 1:
                continue
            dst = layout.path_for(Path(candidates[0]), ".webp")
            dst.parent.mkdir(parents=True, exist_ok=True)
            preview.rename(dst)
            report.moved.append((preview, dst))
            log(f"→ {rel} => {candidates[0]}")
            del orphans[rel]
            del by_basename[Path(rel).name]
            missing.remove(candidates[0])

    if prune_orphans:
        for rel, preview in orphans.items():
            _remove_with_empty_dirs(preview, root)
            report.removed.append(preview)
            log(f"- {rel}")

    for rel in (pbar := tqdm(missing, desc="previews")):
        pbar.set_postfix_str(rel)
        src = originals[rel]
        dst = layout.path_for(Path(rel), ".webp")
        created = _generate(
            src, dst,
            lambda s, d: thumbnail(s, d, size=target.size, quality=target.quality, tools=tools),
        )
        if created:
            report.created["preview"].append(src)
            log(f"+ {dst}")
            # Only newly previewed files are touched: an already-cached file was handled
            # in some earlier run, and re-reading EXIF of the whole tree every minute
            # would defeat the point. Use the `fix-mtime` subcommand for a full pass.
            if fix_mtime:
                fixed = _fix_mtime(src, tools=tools)
                if fixed is not None:
                    report.mtime_fixed.append((src, fixed))
                    log(f"⌚ {src} ← {fixed:%Y-%m-%d %H:%M:%S}")
        elif created is False:
            report.failed.append((src, "preview generation failed"))
            log(f"✗ {src}")


def _sync_fallbacks(
    originals: dict[str, Path],
    target: FallbackTarget,
    layout: MirrorLayout,
    report: SyncReport,
    log: Callable[[str], None],
    *,
    prune_orphans: bool,
    tools: Tools,
) -> None:
    root = layout.cache_dir
    root.mkdir(parents=True, exist_ok=True)

    # Unlike previews there is no move detection here: a moved original just orphans its
    # fallback and gets a fresh one. Only a minority of files need a fallback at all.
    for path in _walk(root):
        rel = str(path.relative_to(root))
        original = _FALLBACK_ORIGINAL_RE.sub("", rel)
        if original not in originals:
            if prune_orphans:
                _remove_with_empty_dirs(path, root)
                report.removed.append(path)
                log(f"- {rel}")
        elif ".part." in path.name and _stale(path):
            path.unlink(missing_ok=True)

    # "Video X was compatible when its mtime was T" — one shared JSON file rather than a
    # sentinel per video, so a folder of thousands of ordinary mp4s neither litters the
    # disk nor costs thousands of ffprobe calls a minute.
    compat_file = root / COMPAT_CACHE_NAME
    compat: dict[str, int] = {}
    if compat_file.is_file():
        try:
            loaded = json.loads(compat_file.read_text())
            if isinstance(loaded, dict):
                compat = {k: v for k, v in loaded.items() if k in originals}
        except (OSError, ValueError):
            pass
    before = dict(compat)

    for rel, src in (pbar := tqdm(originals.items(), desc="fallbacks")):
        pbar.set_postfix_str(rel)
        photo = kind_of(src) == "photo"
        if target.kinds != "both" and (target.kinds == "photo") != photo:
            continue
        dst = layout.path_for(Path(rel), ".jpg" if photo else ".mp4")
        if dst.is_file():
            continue
        fail = _fail_path(dst)
        try:
            src_mtime = int(src.stat().st_mtime)
            if fail.is_file() and fail.stat().st_mtime >= src_mtime:
                continue  # failed before, source unchanged — do not retry every minute
        except OSError:
            continue
        if photo:
            needed = src.suffix.lower() in INCOMPATIBLE_PHOTO_SUFFIXES
        else:
            if compat.get(rel) == src_mtime:
                continue  # probed before, compatible, source unchanged since
            needed = not is_compatible_video(src, tools=tools)
            if not needed:
                compat[rel] = src_mtime
        if not needed:
            continue
        compat.pop(rel, None)
        created = _generate(
            src, dst,
            (lambda s, d: to_jpeg(s, d, quality=target.jpeg_quality)) if photo
            else (lambda s, d: to_h264(s, d, crf=target.crf, tools=tools)),
        )
        if created:
            report.created["fallback"].append(src)
            log(f"+ fallback {dst}")
        elif created is False:
            report.failed.append((src, "fallback generation failed"))
            log(f"✗ fallback {src}")

    if compat != before:
        compat_file.write_text(json.dumps(compat))


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
    log = on_event or logger.info

    source = source.resolve()
    output_dirs = [t.dir.resolve() for t in (previews, fallbacks) if t is not None]

    originals: dict[str, Path] = {}  # rel path -> absolute path, media files only
    for path in _walk(source):
        resolved = path.resolve()
        if any(d == resolved or d in resolved.parents for d in output_dirs):
            continue  # cache dir nested inside the source tree: never sync our own output
        if kind_of(path) != "other":
            originals[str(path.relative_to(source))] = path

    if previews is not None:
        _sync_previews(originals, previews, MirrorLayout(previews.dir, source), report, log, detect_moves=detect_moves,
                       prune_orphans=prune_orphans, fix_mtime=fix_mtime, tools=tools)
    if fallbacks is not None:
        _sync_fallbacks(originals, fallbacks, MirrorLayout(fallbacks.dir, source), report, log,
                        prune_orphans=prune_orphans, tools=tools)
    return report
