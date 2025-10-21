from .convert import Convert


from dataclasses import dataclass
from pathlib import Path


@dataclass
class Slidershow:
    template: Path = Path(__file__).parent.parent / "templates/skelet.html.template"
    url: str = (
        "https://cdn.jsdelivr.net/gh/CZ-NIC/slidershow@0.9.6/slidershow/slidershow.js"
    )


@dataclass
class Env:
    convert: Convert
    slidershow: Slidershow
    file: Path
    sheet: str | None = None
    """ Sheet name to process. If None, all will be processed and multiple files will be generated (if `--output` set).

    Format of the sheet

    Columns:
        comment   filename	start	commands

        comment
          * Inserted HTML comment, displayed at the presenter's notes.
        filename
          * if empty, `start` is header, `commands` is text
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

          Ex: TODO správný? `15, → 4, 1:10`: At 0:15, jump to 0:04, then end at 1:10.

    Rows:
        * If the row starts with the word "SECTION", a new `<section>` is inserted. (And the row is skipped.)
        * Parsing ends on the first empty row.

"""
    output: Path | None = None
    """ By default, the output is printed to the screen. """

    replace_in_filename: list[tuple[str, str]] | None = None
    """ If set, filename from the sheet will be replaced according to this.
    Ex: --replace-filename /mnt/user /mnt/foo jpg JPG -> filename /mnt/user/dir/img.jpg → /mnt/foo/dir/img.JPG
    """

    filename_exist_check: list[Path] | None = None
    """ If the filename is without path and the file does not exist, try finding the file within these dirs. """
