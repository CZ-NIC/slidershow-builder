from .convert import Convert


from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tyro.conf import Positional

from ..collect import Incompatible, PeopleMode
from ..media import Tools
from ..people import DEFAULT_INDEX


@dataclass
class PeopleSelection:
    """Fields shared by `collect --names` and `build --people`: resolving person names
    (and/or bare calendar days) plus search directories into a list of source paths is the
    exact same problem for both, so both take the same flags for it.
    """

    search_dirs: list[Path] = field(default_factory=list, kw_only=True)
    """Directories holding your originals, searched recursively. (required)"""

    names: list[str] = field(default_factory=list, kw_only=True)
    """Person name(s) to select, as spelled in the people index."""

    date_ranges: list[str] = field(default_factory=list, kw_only=True)
    """Day or "from:to" range(s) to select regardless of who is on the photo,
    e.g. 2026-08-05:2026-08-07. Capture time decides, mtime for files that carry none."""

    whole_day: bool = field(default=False, kw_only=True)
    """Also select everything taken on the same day as any --names match — the rest of
    the outing, not just the shots someone happens to be tagged in."""

    index: Path = field(default=DEFAULT_INDEX, kw_only=True)
    """People index (CSV) the --names are looked up in; written by the `people` subcommand.
    Columns `name,filename,taken`:
      name — person's name, exactly as --names spells it (free text)
      filename — base name of the original, no path; found under --search-dirs, case-insensitively
      taken — ISO capture time, or empty; used by --whole-day and to break a tie when a
              filename matches more than one file on disk (see --date-tolerance-hours)
    One row per person *per photo*, e.g. `Jan Novák,IMG_0001.jpg,2026-08-05T10:00:00+00:00`.
    The whole file is read (unlike the sheet, an empty line does not stop parsing) and rows are
    sorted/deduplicated on write, so their order means nothing. Nothing here is Google-specific —
    hand-write the file if you like; Takeout is only the one importer that exists.
    """

    date_cache: bool = field(default=True, kw_only=True)
    """Cache capture times across runs (read whenever --whole-day/--date-ranges is used, or a
    filename matches more than one file on disk)."""

    tools: Tools = field(default_factory=Tools, kw_only=True)
    """Configurable paths to the ffmpeg/ffprobe binaries."""


@dataclass
class Slidershow:
    template: Path = Path(__file__).parent.parent / "templates/skelet.html.template"
    """HTML template the presentation is made of."""

    url: str = (
        "https://cdn.jsdelivr.net/gh/CZ-NIC/slidershow@latest/slidershow/slidershow.js"
    )
    """The URL to be used for generating. Ex. you might want to use an offline local copy of the project."""

    thumb: str | None = None
    """Where the presentation should look for thumbnails — slidershow's inherited `sli-thumb`,
    shown while the full image downloads and used as a video's poster.

    A bare prefix is expanded to the layout `previews`/`collect --preview-dir` writes:
    `previews/` becomes `previews/{file}.webp`. Anything containing `{` is used verbatim;
    the placeholders are {dir} {file} {name} {ext}, e.g. `/cache/preview/{name}-small.{ext}`."""

    fallback: str | None = None
    """Where the presentation should look for browser-playable substitutes of files it cannot
    display (HEIC photo, exotic video codec) — slidershow's `sli-fallback`, a space-separated
    list of candidates it tries in order.

    A bare prefix is expanded to the layout `previews`/`collect --incompatible fallback`
    writes: `fb/` becomes `fb/{file}.jpg fb/{file}.mp4`. Anything containing `{` is verbatim.

    Note both layouts key on the file's *name*, so they line up when the presentation's media
    sit in one folder — which is what `collect` produces. For media scattered over many
    directories, write the template yourself."""


@dataclass
class Env:
    convert: Convert
    slidershow: Slidershow
    file: Path | None = None
    """Spreadsheet (.ods) to build the presentation from. Either this or --dir."""

    dir: Path | None = None
    """Build from a folder instead of a spreadsheet: every media file in it becomes one frame,
    ordered by name. For a folder there is nothing to say beyond "show them", so no points, no
    video commands — that is what the sheet is for. Pair it with `collect`:

        slidershow-builder collect ./trip --search-dirs ~/Photos --names "Jan Novák"
        slidershow-builder build --dir trip --output trip.html
    """

    group_by: Literal["day", "week", "month", "year"] | None = None
    """Only with --dir: order frames by capture time (EXIF/ffprobe, falling back to mtime)
    instead of by name, and insert a section break wherever that value changes from one file
    to the next — e.g. --group-by week starts a new <section> on every ISO week boundary."""

    sheet: str | None = None
    """ Sheet name to process. If None, all will be processed and multiple files will be generated (if `--output` set).

    Format of the sheet

    Columns:
        comment   filename	start	commands

        Comment is an inserted HTML comment, displayed at the presenter's notes.

    Text frame
      filename: <empty>
      start: header
      commands: subtitle

    Image frame
      start: point
        [left = 0, top = 0, scale = 1, transition_duration = 0, duration = 0, data-rotate = 0]
        Ex: `[[], [100,100,5]]` will begin unzoomed, on the next frame we zoom to 5.

        See: https://github.com/CZ-NIC/slidershow/?tab=readme-ov-file#data-step-points

        How to get the point? Go to the slidershow in the browser, open properties Alt+p and click on the new point.

    Video frame
        start
          * video start time, empty = 0:00
        commands
          * number is a timestamp, jehož akce určují následující buňky
          * posouvací šipka, ex: `→60.5` skočí z momentu na 60.5
          * rate a číslo změní rate momentu `rate 2`
          * mute, unmute
          * R+number(M|U): rate. Ex: `R2` = rate 2, `R4M` = rate 4 + mute
          * P = rate 1, unmute
          * F+number: faster rate. Ex `F2` = rate 1.2
          * comma character behaves like a cell-separator, these are independent commands `rate 2, unmute` → `rate 2` a `unmute`
          * poslední osamělé číslo je end
          * point command zooms, ex: `point:[0,0,2,null,null,270]` zoom and rotate. (Point musí být v buňce zvlášť.)

          Ex: `15, → 4, 1:10`: At 0:15, jump to 0:04, then end at 1:10.

    Section break
      comment: SECTION

      If the row starts with the word "SECTION", a new `<section>` is inserted. (And the row is skipped.)

    Rows:
      Parsing ends on the first empty row.

"""
    output: Path | None = None
    """ By default, the output is printed to the screen. """

    replace_in_filename: list[tuple[str, str]] | None = None
    """ If set, filename from the sheet will be replaced according to this.
    Ex: --replace-filename /mnt/user /mnt/foo jpg JPG -> filename /mnt/user/dir/img.jpg → /mnt/foo/dir/img.JPG
    """

    filename_autosearch: list[Path] | None = None
    """ If the filename is without path and the file does not exist, try finding the file within these dirs. """

    filename_autosearch_cache: bool = True
    """Use a cache file for filename_autosearch, persistent accress program launches. """

    read_exif: bool = True
    """ Adds EXIF info to the HTML. The same way as if the file was dragged into a live Slidershow session """

    read_exif_cache: bool = True
    """Cache read EXIF (path|size|mtime) across program launches, so a repeated build over
    the same files does not re-read/re-decode their EXIF every time."""


@dataclass
class Build(Env, PeopleSelection):
    """Generate a slidershow HTML from a spreadsheet (.ods), a folder, or people selection.

    This is the default subcommand: `slidershow-builder --file x.ods` is equivalent
    to `slidershow-builder build --file x.ods`.

    A third mode, alongside --file/--dir: pass --names and/or --date-ranges (the same
    flags `collect` takes) to resolve people straight to source paths and render them,
    with no intermediate symlink folder:

        slidershow-builder build --names "Jan Novák" --search-dirs ~/Photos --output trip.html
    """

    people_mode: PeopleMode = "union"
    """Only meaningful with several --names. union (default): photos with at least one of
    them. intersection: only photos where all of them appear together."""

    dump_sheet: Path | None = None
    """Instead of rendering HTML, write the resolved+ordered+sectioned --people selection as
    an .ods spreadsheet in the format --sheet documents, for hand-tuning in LibreOffice before
    a normal `build --file`. Only valid together with --names/--date-ranges."""


@dataclass
class Previews:
    """Sync a media tree: generate thumbnails + fallback conversions for browser-incompatible
    files, detect moved files, prune orphans, optionally fix mtime from capture time."""

    source: Positional[Path]
    """Root directory of the source media tree."""

    preview_dir: Path | None = None
    """Where to write thumbnails (mirrors the source tree structure). None = skip previews."""

    fallback_dir: Path | None = None
    """Where to write browser-incompatible fallback conversions (HEIC->JPEG, HEVC->H.264).
    None = skip fallbacks."""

    fallback_kinds: Literal["both", "photo", "video"] = "both"
    """Restrict --fallback-dir to photos or to videos. Transcoding video costs orders of
    magnitude more than converting a photo (hours of CPU, tens of GB on a large tree), so
    `photo` gets the cheap half done on its own."""

    size: int = 320
    """Thumbnail long-edge size in px."""

    quality: int = 75
    """Thumbnail WebP quality."""

    fix_mtime: bool = True
    """Set file mtime from EXIF/ffprobe capture time. Safe over a folder of symlinks (what
    `collect` builds): a symlink gets its own mtime, the original it points at is never
    written to."""

    detect_moves: bool = True
    """Detect renamed/moved files by basename instead of regenerating."""

    prune_orphans: bool = True
    """Delete previews/fallbacks whose source no longer exists."""

    json: bool = False
    """Print the sync report as JSON instead of a text summary."""

    tools: Tools = field(default_factory=Tools)
    """Configurable paths to the ffmpeg/ffprobe binaries."""


@dataclass
class FixMtime:
    """Backfill file mtime from EXIF/ffprobe capture time (`--backfill-mtime` of old).

    Safe over a folder of symlinks (what `collect` builds): a symlink gets its own mtime,
    the original it points at is never written to."""

    source: Positional[Path]
    """Directory to walk and backfill mtime on."""

    tools: Tools = field(default_factory=Tools)
    """Configurable paths to the ffmpeg/ffprobe binaries."""


@dataclass
class Probe:
    """Debug: print codec, browser-compatibility, capture time for a single file."""

    file: Positional[Path]

    tools: Tools = field(default_factory=Tools)
    """Configurable paths to the ffmpeg/ffprobe binaries."""


@dataclass
class Collect(PeopleSelection):
    """Symlink originals scattered over several disks into one folder ready for slidershow.

    Selects them by the people tagged on them (`people` subcommand's index) and/or by the
    day they were taken, then makes DEST a folder of symlinks — no data is duplicated and
    nothing is ever written into the originals.

    Ex: slidershow-builder collect ./trip --search-dirs ~/Photos /mnt/backup \
            --names "Jan Novák" --whole-day --date-ranges 2026-08-05:2026-08-07

    Repeating a long invocation is what `--config jobs.yaml` is for; put the options under
    a `collect:` key there.
    """

    dest: Positional[Path]
    """Folder to fill with symlinks. Created if missing; re-running only adds what is new."""

    incompatible: Incompatible = "replace"
    """What to do with files no browser can display (HEIC/HEIF photos, exotic video codecs):
    * replace  — write a converted .jpg/.mp4 into DEST instead of the symlink, so the folder
                 is displayable as it stands (drag it into slidershow and it just works);
    * fallback — symlink the original and put the conversion in --fallback-dir, the layout
                 slidershow's `sli-fallback` attribute expects;
    * link     — symlink only, leave it undisplayable."""

    fallback_dir: Path | None = None
    """Where `--incompatible fallback` writes its conversions (default: DEST/.fallback)."""

    preview_dir: Path | None = None
    """Also generate 320px WebP thumbnails here, mirroring DEST (for `sli-thumb`)."""

    date_tolerance_hours: float = 12.0
    """A filename matching more than one file on disk (a camera's counter can repeat across
    years) is resolved by comparing each candidate's own capture time to the index's tagged
    `taken` date; this is how far apart the two may be and still count as the same photo.
    Kept generous by default because `taken` usually came through a different pipeline
    (e.g. Google Photos) than the file's own EXIF and the two can disagree by a few hours."""

    interactive: bool = True
    """When a filename's candidates can't be told apart even with --date-tolerance-hours, ask
    which one to use instead of silently taking the first (a dialog, batching every such case
    into one screen). False keeps the old silent behavior — for unattended/cron runs."""

    set_mtime: bool = True
    """Stamp each symlink's own mtime with the photo's capture time, so DEST sorts
    chronologically. The originals are never touched."""

    dry_run: bool = False
    """Print what would happen, create nothing."""

    json: bool = False
    """Print the report as JSON instead of a text summary."""


@dataclass
class People:
    """Show — or build — the index of who is on which photo, which `collect --names` selects by.

    The index is a plain `name,filename,taken` CSV: hand-writable, mergeable, and not tied to
    any one photo service. Google Takeout is currently the only importer, because Google Photos
    exposes face tags nowhere else; --takeout-dir reads them straight out of the downloaded
    zip(s), without extracting a single photo, and merges the result into the existing index.
    """

    takeout_dir: Path | None = None
    """Directory with the Google Takeout zip file(s). Omit to just list what the index holds."""

    index: Path = DEFAULT_INDEX
    """The CSV to read and, when importing, merge into."""
