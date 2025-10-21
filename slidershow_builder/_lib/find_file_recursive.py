import json
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# Define persistent cache file
cache_dir = Path.home() / ".cache" / "slidershow_builder"
cache_dir.mkdir(parents=True, exist_ok=True)
cache_file = cache_dir / "file_cache.json"
cache = {}


CACHE_FILE = Path.home() / ".cache" / "slidershow_builder" / "file_cache.json"
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

cache: dict[str, str] = {}

@contextmanager
def filename_cache(enabled: bool):
    """
    Context manager for persistent filename cache.

    Usage:
        with filename_cache(m.env.filename_autosearch_cache):
            ...
    """
    global cache
    if enabled and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
        except json.JSONDecodeError:
            pass
    try:
        yield
    finally:
        if enabled:
            with open(CACHE_FILE, "w") as f:
                json.dump(cache, f)

def find_file_recursive(name: str, directories: list[Path]) -> Optional[Path]:
    """
    Return the first path matching the filename recursively.

    If the filename is already in the cache, return the cached path
    without searching directories.

    Args:
        name: The filename to search for.
        directories: List of directories to search recursively.
        cache: Dictionary mapping filenames to their cached paths.

    Returns:
        Full resolved Path if found, otherwise None.
    """
    # Check persistent cache first
    if name in cache:
        cached_path = Path(cache[name])
        if cached_path.exists() and cached_path.is_file():
            return cached_path.absolute()
        else:
            # Remove invalid cache entry
            del cache[name]

    # Search directories recursively
    for d in directories:
        if not d.is_dir():
            continue
        for p in d.rglob(name):
            if p.is_file():
                cache[name] = str(p.absolute())
                return p.absolute()
    return None

