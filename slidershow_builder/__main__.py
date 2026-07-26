#!/usr/bin/env python3
import json
import logging
import sys

from mininterface import run
from tyro.conf import DisallowNone, FlagCreatePairsOff

from ._lib.env import Build, FixMtime, Previews, Probe
from ._lib.find_file_recursive import filename_cache
from ._lib.optional_deps import MissingOptionalDependency
from .media import fix_mtime as _fix_mtime, probe as _probe
from .sync import FallbackTarget, PreviewTarget, sync_tree

logger = logging.getLogger(__name__)

SUBCOMMANDS = {"build", "previews", "fix-mtime", "probe"}


def _argv_with_implicit_build(argv: list[str]) -> list[str]:
    """`slidershow-builder --file x.ods` keeps working without a `build` subcommand."""
    if not argv:
        return ["build"]
    if argv[0] in SUBCOMMANDS or argv[0] in ("-h", "--help"):
        return argv
    return ["build", *argv]


def _run_build(env: Build):
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
    fallbacks = FallbackTarget(env.fallback_dir) if env.fallback_dir else None
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
            Previews,
            FixMtime,
            Probe,
        ],
        args=_argv_with_implicit_build(sys.argv[1:]),
    )

    try:
        match m.env:
            case Build():
                _run_build(m.env)
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
