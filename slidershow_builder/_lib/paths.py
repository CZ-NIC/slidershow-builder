"""Where the tool keeps its own state, XDG-aware.

Nothing may default to a path next to the source files: once installed with pip the
package lives in site-packages, where a `people.csv` default would be both unwritable
and invisible.
"""

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "slidershow_builder"


CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
"""Regenerable from the sources alone — safe to delete at any time."""

DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share")
"""Not regenerable: `people.csv` needs the Takeout export, which is usually long gone."""
