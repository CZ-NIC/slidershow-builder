#!/usr/bin/env python3
import json
import logging
import sys

from mininterface import run
from tyro.conf import DisallowNone, FlagCreatePairsOff

from . import people as _people
from ._lib.env import Build, Collect, FixMtime, People, Previews, Probe
from ._lib.find_file_recursive import filename_cache
from ._lib.optional_deps import MissingOptionalDependency
from .collect import collect as _collect, parse_days
from .media import fix_mtime as _fix_mtime, probe as _probe
from .sync import FallbackTarget, PreviewTarget, sync_tree

logger = logging.getLogger(__name__)

SUBCOMMANDS = {"build", "collect", "people", "previews", "fix-mtime", "probe"}


def _argv_with_implicit_build(argv: list[str]) -> list[str]:
    """`slidershow-builder --file x.ods` keeps working without a `build` subcommand."""
    if not argv:
        return ["build"]
    if argv[0] in SUBCOMMANDS or argv[0] in ("-h", "--help"):
        return argv
    return ["build", *argv]


def _run_build(env: Build):
    if bool(env.file) == bool(env.dir):
        raise SystemExit("Pass either --file (a spreadsheet) or --dir (a folder of media)")

    if env.dir:
        try:
            from ._lib.process import process_dir
        except ImportError as e:
            raise MissingOptionalDependency("Building a slidershow", "build") from e
        if not env.dir.is_dir():
            raise SystemExit(f"Not a directory: {env.dir}")
        process_dir(_BuildShim(env), env.dir)
        return

    try:
        import ezodf

        from ._lib.process import process_sheet
    except ImportError as e:
        raise MissingOptionalDependency("Building a slidershow from a spreadsheet", "build") from e

    if not env.file.exists():
        print("File does not exists", env.file)
        quit()
    sheets = ezodf.opendoc(env.file).sheets

    if env.sheet:
        for s in sheets:
            if s.name == env.sheet:
                sheets = [s]
                break
        else:
            raise ValueError(f"Sheet {env.sheet} not found")
        suffix = False
    else:
        suffix = True

    with filename_cache(env.filename_autosearch_cache):
        for sheet in sheets:
            process_sheet(_BuildShim(env), suffix, sheet)


class _BuildShim:
    """process_sheet expects `m.env.*` — keep it unchanged, adapt Build here instead."""

    def __init__(self, env: Build):
        self.env = env


def _run_previews(env: Previews):
    previews = PreviewTarget(env.preview_dir, env.size, env.quality) if env.preview_dir else None
    fallbacks = (FallbackTarget(env.fallback_dir, kinds=env.fallback_kinds)
                 if env.fallback_dir else None)
    report = sync_tree(
        env.source,
        previews=previews,
        fallbacks=fallbacks,
        fix_mtime=env.fix_mtime,
        detect_moves=env.detect_moves,
        prune_orphans=env.prune_orphans,
        tools=env.tools,
    )
    if env.json:
        print(json.dumps(report.to_json(), indent=2))
    else:
        print(
            f"Created {len(report.created['preview'])} previews, "
            f"{len(report.created['fallback'])} fallbacks; "
            f"moved {len(report.moved)}, removed {len(report.removed)}, "
            f"failed {len(report.failed)}, mtime fixed {len(report.mtime_fixed)}"
        )


def _run_collect(env: Collect):
    filenames: set[str] = set()
    person_days: set[str] = set()
    if env.names:
        rows = _people.read_index(env.index)
        if not rows:
            raise SystemExit(f"No people index at {env.index} — run `slidershow-builder people --takeout-dir ...` first")
        filenames, person_days = _people.files_and_days(rows, set(env.names))
        if not filenames:
            known = ", ".join(sorted(_people.counts(rows))) or "(none)"
            raise SystemExit(f"Nobody named {sorted(env.names)!r} in {env.index}. Known: {known}")
    days = parse_days(env.date_ranges)
    if not filenames and not days:
        raise SystemExit("Nothing to collect: pass --names and/or --date-ranges")
    if not env.search_dirs:
        raise SystemExit("--search-dirs is required (one or more directories holding your originals)")

    fallback_dir = env.fallback_dir or env.dest / ".fallback"
    report = _collect(
        env.dest,
        search_dirs=env.search_dirs,
        filenames=filenames,
        days=days,
        whole_day_of=person_days if env.whole_day else (),
        incompatible=env.incompatible,
        set_mtime=env.set_mtime,
        dry_run=env.dry_run,
        date_cache=env.date_cache,
        tools=env.tools,
        excluded=[d for d in (fallback_dir, env.preview_dir) if d],
        on_event=print,
    )

    # Thumbnails and the `fallback` layout are exactly what `previews` does — run it over
    # the folder we just built rather than growing a second copy of that machinery here.
    # fix_mtime stays off: collect already stamped the links, and this must not re-walk them.
    wants_fallbacks = env.incompatible == "fallback"
    if not env.dry_run and (env.preview_dir or wants_fallbacks):
        sync_tree(
            env.dest,
            previews=PreviewTarget(env.preview_dir) if env.preview_dir else None,
            fallbacks=FallbackTarget(fallback_dir) if wants_fallbacks else None,
            fix_mtime=False,
            tools=env.tools,
            on_event=print,
        )

    if env.json:
        print(json.dumps(report.to_json(), indent=2))
        return
    print(f"\nSymlinks: {len(report.linked)} new, {report.kept} already there")
    if report.converted:
        print(f"Converted into {env.dest}: {len(report.converted)}")
    if filenames:
        matched = len(filenames) - len(report.missing)
        print(f"Direct name match: {matched}/{len(filenames)}")
    if report.failed:
        print(f"Failed: {len(report.failed)}")
    if report.missing:
        print(f"Not found ({len(report.missing)}):")
        for filename in report.missing:
            print(f"  {filename}")


def _run_people(env: People):
    rows = _people.read_index(env.index)
    if env.takeout_dir:
        before = len(rows)
        rows |= _people.import_takeout(env.takeout_dir)
        _people.write_index(env.index, rows)
        print(f"{len(rows) - before} new records, {len(rows)} in {env.index}\n")
    elif not rows:
        raise SystemExit(f"No people index at {env.index} — build one with --takeout-dir DIR")
    for name, count in _people.counts(rows).most_common():
        print(f"{name}: {count}")


def _run_fix_mtime(env: FixMtime):
    count = 0
    for path in env.source.rglob("*"):
        if path.is_file() and _fix_mtime(path, tools=env.tools) is not None:
            count += 1
    print(f"Fixed mtime on {count} files")


def _run_probe(env: Probe):
    info = _probe(env.file, tools=env.tools)
    print(info)


def main():
    m = run(
        [
            DisallowNone[FlagCreatePairsOff[Build]],
            Collect,
            People,
            Previews,
            FixMtime,
            Probe,
        ],
        args=_argv_with_implicit_build(sys.argv[1:]),
        add_config=True,  # `--config jobs.yaml`, keyed by subcommand name
    )

    try:
        match m.env:
            case Build():
                _run_build(m.env)
            case Collect():
                _run_collect(m.env)
            case People():
                _run_people(m.env)
            case Previews():
                _run_previews(m.env)
            case FixMtime():
                _run_fix_mtime(m.env)
            case Probe():
                _run_probe(m.env)
    except MissingOptionalDependency as e:
        print(e)
        sys.exit(1)


if __name__ == "__main__":
    main()
