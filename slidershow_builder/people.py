"""Who is on which photo — a name index the `collect` subcommand selects by.

The index itself is deliberately dumb and open: a three-column CSV
``name,filename,taken``, sorted, deduplicated, mergeable. Nothing in it is
Google-specific, so it can equally be written by hand, exported from another
photo manager, or produced by a future importer here — `collect` only ever
sees `read_index()`.

`import_takeout()` is the one proprietary piece, and it is an *importer*, not
the format: Google Photos' API does not expose face tags at all and Takeout has
no metadata-only export, so the only way to get them is to read the `.json`
sidecars out of the downloaded Takeout zip — which this does without extracting
a single photo.
"""

import csv
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from ._lib.paths import DATA_DIR

DEFAULT_INDEX = DATA_DIR / "people.csv"
FIELDS = ("name", "filename", "taken")

Row = tuple[str, str, str]
"""(person name, original file name, ISO capture time or "")"""


def read_index(path: Path) -> set[Row]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {(row["name"], row["filename"], row["taken"]) for row in csv.DictReader(f)}


def write_index(path: Path, rows: Iterable[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FIELDS)
        writer.writerows(sorted(rows))


def counts(rows: Iterable[Row]) -> Counter:
    return Counter(name for name, _, _ in rows)


def files_and_days(rows: Iterable[Row], names: set[str]) -> tuple[set[str], set[str]]:
    """File names tagged with any of `names`, plus the calendar days they were taken on."""
    filenames, days = set(), set()
    for name, filename, taken in rows:
        if name in names:
            filenames.add(filename)
            if taken:
                days.add(taken[:10])  # YYYY-MM-DD
    return filenames, days


def taken_hints(rows: Iterable[Row], names: set[str]) -> dict[str, datetime]:
    """Per-file tagged capture time for `names`, for `collect` to break a filename tie with
    (see `Collect.date_tolerance_hours`) — only the rows that actually carry one."""
    hints: dict[str, datetime] = {}
    for name, filename, taken in rows:
        if name in names and taken:
            hints[filename] = datetime.fromisoformat(taken)
    return hints


def files_by_name(rows: Iterable[Row], names: set[str]) -> dict[str, set[str]]:
    """File names tagged with each of `names`, kept apart — `files_and_days` collapses
    several names into one combined set, which is exactly what an intersection query
    (`--people-mode intersection`) cannot use."""
    result: dict[str, set[str]] = {name: set() for name in names}
    for name, filename, _taken in rows:
        if name in names:
            result[name].add(filename)
    return result


def days_for_files(rows: Iterable[Row], filenames: set[str]) -> set[str]:
    """Calendar days any of `filenames` was taken on, per the index's `taken` column."""
    return {taken[:10] for _name, filename, taken in rows if filename in filenames and taken}


# --- Google Takeout importer ------------------------------------------------

def _iter_sidecars(takeout_dir: Path) -> Iterator[tuple[str, dict]]:
    zips = sorted(takeout_dir.glob("*.zip"))
    if not zips:
        raise SystemExit(f"No *.zip found in {takeout_dir}")
    for zip_path in zips:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                if ("Google Photos/" in name and name.endswith(".json")
                        and not name.endswith("/metadata.json")):
                    with zf.open(name) as f:
                        try:
                            yield name, json.load(f)
                        except ValueError:
                            continue


def _original_filename(json_name: str) -> str:
    stem = Path(json_name).stem  # strip the trailing ".json"
    suffix = ".supplemental-metadata"
    return stem[: -len(suffix)] if stem.endswith(suffix) else stem


def _taken(data: dict) -> str:
    ts = data.get("photoTakenTime", {}).get("timestamp")
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def import_takeout(takeout_dir: Path) -> set[Row]:
    """Face tags out of every Takeout zip in `takeout_dir`, photo bytes untouched.

    A photo that also sits in an album is exported twice (once under
    `Photos from <year>/`, once under the album), so the same person can be counted
    twice under two different file names.
    """
    rows: set[Row] = set()
    for name, data in _iter_sidecars(takeout_dir):
        people = data.get("people") or []
        if not people:
            continue
        filename, taken = _original_filename(name), _taken(data)
        for person in people:
            person_name = (person.get("name") or "").strip()
            if person_name:
                rows.add((person_name, filename, taken))
    return rows
