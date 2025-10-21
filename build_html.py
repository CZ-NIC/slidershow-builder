#!/usr/bin/env python3
import logging
from dataclasses import dataclass
from pathlib import Path
from re import match
from typing import Optional

import ezodf
from mininterface import run
from tqdm import tqdm

from convert import IMAGE_SUFFIXES, Convert

logger = logging.getLogger(__name__)


@dataclass
class Env:
    convert: Convert
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

    template = Path(__file__).parent / "skelet.html.template"

    replace_in_filename: list[tuple[str, str]] | None = None
    """ If set, filename from the sheet will be replaced according to this.
    Ex: --replace-filename /mnt/user /mnt/foo jpg JPG -> filename /mnt/user/dir/img.jpg → /mnt/foo/dir/img.JPG
    """

    filename_exist_check: list[Path] | None = None
    """ If the filename is without path and the file does not exist, try finding the file within these dirs. """

    slidershow_url: str = "https://cdn.jsdelivr.net/gh/CZ-NIC/slidershow@0.9.6/slidershow/slidershow.js"


TEMPLATE = """<article data-video-points='[{points}]'><video controls="controls" data-src="{src}"></video></article>"""
TEMPLATE_IMG = (
    """<article data-step-points='{points}'><img data-src="{src}"/></article>"""
)
TEMPLATE_TEXT = """<article class="main">
                    <h1>{title}</h1>
                    <p>{text}</p>
                </article>"""
TNUM = (
    r"(\d*(?::\d+)?\.?\d+?)"  # time number, a number including decimal dot and a colon
)
NUM = r"(\d*\.?\d+?)"  # a number including decimal dot, ex '3', '1.3', '.3'

clist = {
    "mute": "mute",
    "unmute": "unmute",
    "M": "mute",
    "U": "unmute",
    "pause": "pause",
}


def output_tokens(moment, tokens):
    return f'[{moment}, {",".join(f'"{t}"' for t in tokens)}]'


def tim(v: str) -> int:
    """Convert number in format min:sec to total seconds. `1:32.2` → 92.2, `5` → 5"""
    if not ":" in v:
        return v
    minutes, seconds = map(float, v.split(":"))
    total_seconds = minutes * 60 + seconds
    return total_seconds


def parse_commands(start: str | None, commands: list[str]):
    moment: str | float | int | None = None
    tokens = []

    if start and start != "0":
        commands.insert(0, "0")
        commands.insert(1, f"→{tim(start)}")

    # Make pipe behave like the cell-barrier.
    # `["→2", "5,→7|9,→11"]` -> `["→2", "5,→7", "9,→11"]`
    commands = [part for cmd in commands for part in (cmd or "").split("|") if part]

    # filter out trailing empty cells
    # What does this do?
    # `rate1,M` → `rate1` , `M`
    # `point:[161.2,204.9,5]` stays the same
    commands = [
        r.strip()
        for subcommand in commands
        if subcommand is not None
        for r in ([subcommand] if "[" in subcommand else subcommand.split(","))
    ]
    logger.info(commands)
    for i, command in enumerate(commands):
        if m := match(rf"{TNUM}$", command):
            if moment or tokens:
                if not tokens:
                    raise ValueError(f"No action at {command} at: {commands}")
                if not moment:
                    moment = "0"
                yield output_tokens(moment, tokens)

            moment = tim(m[0])
            tokens.clear()
            if i == len(commands) - 1:  # end number, stop at this moment1
                yield f'[{tim(m[0])}, "pause"]'
            continue
        elif m := match(rf"→\s?{TNUM}$", command):
            tokens.append(f"goto:{tim(m[1])}")
        elif m := match(rf"{TNUM}\s?→\s?{TNUM}", command):
            if moment:
                raise ValueError(
                    f"Moment already defined, moment {moment} while processing {command} at {commands}"
                )
            yield f'[{tim(m[1])}, "goto:{tim(m[2])}"]'
        elif m := match(rf"F{NUM}(M|U)?", command):  # faster 1.N
            tokens.append(f"rate:1.{m[1]}")
            if m.group(2):
                tokens.append(clist[m[2]])
        elif m := match(rf"R{NUM}(M|U)?", command):
            tokens.append(f"rate:{m[1]}")
            if m.group(2):
                tokens.append(clist[m[2]])
        elif m := match(rf"rate\s?{NUM}", command):
            tokens.append(f"rate:{m[1]}")
        elif command in clist:
            tokens.append(clist[command])
        elif command.startswith("point"):
            tokens.append(command)
        elif m := match(rf"P", command):  # as play
            tokens.append(f"rate:1")
            tokens.append("unmute")
        elif command.startswith("TODO"):
            logger.warning(command)  # undocumented feature
        else:
            raise ValueError(f"Unknown command {command} at {commands}")
    if tokens:
        yield output_tokens(moment or "0", tokens)


def find_file_recursive(name: str, directories: list[Path]) -> Optional[Path]:
    """Return first path matching the name recursively."""
    for d in directories:
        if not d.is_dir():
            continue
        for p in d.rglob(name):
            if p.is_file():
                return p.resolve()
    return None


def is_plain_filename(p: Path) -> bool:
    """True, if Path is just a file name without path."""
    return not p.is_absolute() and p.parent == Path(".")


def cell_value(val):
    if val is None:
        return val
    if isinstance(val, (int, float)):
        return str(val).replace(".0", "")
    return str(val)


if __name__ == "__main__":
    m = run(Env)
    if not m.env.file.exists():
        print("File does not exists", m.env.file)
        quit()
    sheets = ezodf.opendoc(m.env.file).sheets

    if m.env.sheet:
        for s in sheets:
            if s.name == m.env.sheet:
                sheets = [s]
                break
        else:
            raise ValueError(f"Sheet {m.env.sheet} not found")
        suffix = False
    else:
        suffix = True

    for sheet in sheets:
        print(f"Processing: {m.env.file} / {sheet.name}")
        output = []
        for row in (pbar:=tqdm(list(sheet.rows())[1:])):
            comment, filename, start, *commands = [
                cell_value(cell.value) for cell in row
            ]

            if comment == "SECTION":
                output.append("</section><section>")
                continue
            if not any((comment, filename, start, *commands)):
                print("EARLY STOP on empty row")
                break

            if filename:  # media frame
                # parse commands
                path = Path(filename)
                pbar.set_postfix_str(path.name)
                suff = path.suffix.lower()
                if suff in IMAGE_SUFFIXES:
                    template = TEMPLATE_IMG
                    points = start or ""
                    if any(c.strip() for c in commands if c):
                        logger.warning(
                            f"commands are being ignored for img '{filename}' {commands}"
                        )
                else:
                    template = TEMPLATE
                    try:
                        points = ",".join(parse_commands(start, commands))
                    except ValueError as e:
                        e.add_note(f"At filename: {filename}")
                        raise

                # change the name
                if m.env.replace_in_filename:
                    for args in m.env.replace_in_filename:
                        path = Path(filename.replace(*args))
                        filename = str(path)

                if (
                    m.env.filename_exist_check
                    and not path.exists()
                    and is_plain_filename(path)
                ):
                    if p := find_file_recursive(filename, m.env.filename_exist_check):
                        path = p
                        filename = str(path)
                    else:
                        logger.warning("Filename %s does not exist", filename)

                # convert to cache
                filename_used = m.env.convert.run(path)

                out = template.format(points=points, src=filename_used)
            elif start or commands:  # text frame
                out = TEMPLATE_TEXT.format(
                    title=start, text="".join(str(a) for a in commands if a)
                )
            else:
                raise ValueError

            if m.env.output:
                if comment:
                    output.append(f"<!-- {comment} -->")
                output.append(out)
            else:
                if comment:
                    print(comment)
                print(out)

        if fname := m.env.output:
            if suffix:
                fname = fname.with_name(f"{fname.stem}_{sheet.name}{fname.suffix}")
            fname.write_text(
                m.env.template.read_text().format(contents="\n".join(output), slidershow_url=m.env.slidershow_url)
            )
            print("Written to", fname)
