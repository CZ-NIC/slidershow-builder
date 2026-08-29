#!/usr/bin/env python3
import logging
from datetime import date
from pathlib import Path
from re import match
from typing import Callable
from jinja2 import Template

from tqdm import tqdm

from .exif import read_exif

from ..collect import DateIndex
from ..media import DEFAULT_TOOLS, capture_time, kind_of
from .find_file_recursive import find_file_recursive
from .convert import IMAGE_SUFFIXES

logger = logging.getLogger(__name__)

# TODO we should put exif to the <article> - add to the changelog
# TODO we accept csv, or a mere dir that we crawl

# Attribute names are slidershow's own `sli-<property>`, the only spelling `prop()` resolves
# (verified against the released bundle); the `data-` names of old are not read at all.
TEMPLATE_VIDEO = Template(
    """<article{% if points %} sli-video-points='[{{ points }}]'{% endif %}><video controls="controls" sli-src="{{ src }}" {% if datetime %} sli-datetime="{{ datetime }}"{% endif %}></video></article>"""
)
TEMPLATE_IMG = Template(
    """<article{% if points %} sli-step-points='{{ points }}'{% endif %}><img sli-src="{{ src }}" {% if datetime %} sli-datetime="{{ datetime }}"{% endif %}{% if device %} sli-device="{{ device }}"{% endif %}{% if gps %} sli-gps="{{ gps[0] }}, {{ gps[1] }}"{% endif %}/></article>"""
)
#: The layout `previews`/`collect` write, expressed in slidershow's `{dir} {file} {name} {ext}`
#: placeholders — the suffix is appended to the whole file name, see `cache.MirrorLayout`.
THUMB_LAYOUT = "{prefix}{{file}}.webp"
FALLBACK_LAYOUT = "{prefix}{{file}}.jpg {prefix}{{file}}.mp4"


def media_attributes(thumb: str | None, fallback: str | None) -> str:
    """`sli-thumb`/`sli-fallback` for <main>; frames inherit them (slidershow's `prop()`).

    A value with no `{` is a bare directory prefix and gets expanded into the layout our own
    `previews`/`collect` produce; anything else is the user's own template, passed through.
    """
    out = ""
    for name, value, layout in (("sli-thumb", thumb, THUMB_LAYOUT),
                                ("sli-fallback", fallback, FALLBACK_LAYOUT)):
        if not value:
            continue
        if "{" not in value:
            value = layout.format(prefix=value if value.endswith("/") else value + "/")
        out += f' {name}="{value}"'
    return out


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


def is_plain_filename(p: Path) -> bool:
    """True, if Path is just a file name without path."""
    return not p.is_absolute() and p.parent == Path(".")


def cell_value(val):
    if val is None:
        return val
    if isinstance(val, (int, float)):
        return str(val).replace(".0", "")
    return str(val)


def write_presentation(m, output: list[str], fname: Path | None, section_title: str | None = None) -> None:
    template = m.env.slidershow.template.read_text()
    attrs = media_attributes(m.env.slidershow.thumb, m.env.slidershow.fallback)
    if attrs and "{main_attrs}" not in template:
        logger.warning("--slidershow.template has no {main_attrs} placeholder, "
                       "so --slidershow.thumb/--slidershow.fallback have no effect")
    if not fname:
        return
    section_attrs = f' sli-title="{section_title}"' if section_title else ""
    fname.write_text(template.format(
        contents="\n".join(output), slidershow_url=m.env.slidershow.url, main_attrs=attrs,
        section_attrs=section_attrs))
    print("Written to", fname)


def render_media(m, path: Path, points: str = "") -> str:
    """One media frame. `src` stays exactly the path we were given (relative paths in the
    sheet/directory are what the HTML has to keep, they are resolved by the browser)."""
    if kind_of(path) == "video":
        dt = capture_time(path, tools=DEFAULT_TOOLS) if m.env.read_exif else None
        return TEMPLATE_VIDEO.render(points=points, src=path, datetime=dt.isoformat() if dt else None)
    device, gps, dt = read_exif(path) if m.env.read_exif else (None, None, None)
    if m.env.read_exif and not dt:
        # piexif reads JPEG/TIFF only; HEIC (and any exif-less JPEG) falls back to Pillow.
        dt = capture_time(path, tools=DEFAULT_TOOLS)
    return TEMPLATE_IMG.render(points=points, src=path,
                               datetime=dt.isoformat() if dt else None, device=device, gps=gps)


def process_dir(m, directory: Path):
    """Turn a folder of media into a presentation: one frame per file, ordered by name — or,
    with `--group-by`, by capture time, with a section break wherever that day/week/month/year
    changes.

    The counterpart of `process_sheet` for the case there is nothing to say about the files
    beyond "show them" — notably a folder built by `collect`. Paths in the HTML stay relative
    to `--output`'s directory when `--dir` is given relative, which is what makes the result
    portable next to the media.
    """
    files = [p for p in directory.rglob("*") if p.is_file() and kind_of(p) != "other"]
    if not files:
        raise ValueError(f"No media files in {directory}")

    group_by = m.env.group_by
    if group_by:
        # Same cache `collect --whole-day`/`previews` use, keyed by path|size|mtime, so a
        # rebuild of the same folder does not ffprobe/re-read EXIF of every file again.
        # `sort(key=...)` has no per-item hook of its own, and reading a HEIC/video's date can
        # stall on slow storage, so the dates are read in their own tqdm-visible pass first.
        dates = DateIndex(DEFAULT_TOOLS)
        dated = []
        for path in (pbar := tqdm(files, desc="reading dates")):
            pbar.set_postfix_str(path.name)
            dated.append((dates.taken(path), path))
        dated.sort(key=lambda pair: pair[0])
        files = [path for _, path in dated]
    else:
        files.sort()

    print(f"Processing: {directory} ({len(files)} media files)")

    output = []
    prev_key = None
    first_key = None
    for path in (pbar := tqdm(files)):
        pbar.set_postfix_str(path.name)
        if group_by:
            key = dates.group_key(path, group_by)
            if prev_key is None:
                first_key = key
            elif key != prev_key:
                output.append(f'</section><section sli-title="{key}">')
            prev_key = key
        out = render_media(m, Path(m.env.convert.run(path)))
        if m.env.output:
            output.append(out)
        else:
            print(out)
    if group_by:
        dates.save()
    write_presentation(m, output, m.env.output, section_title=first_key)


def day_title(day: str) -> str:
    """`"2026-08-05"` -> a human-readable title for that day's section/title frame."""
    return date.fromisoformat(day).strftime("%A, %B %d, %Y")


def process_people(m, paths: list[Path], day_of: Callable[[Path], str]) -> None:
    """Render an already-resolved, chronologically-sorted list of paths (`build --people`)
    as a presentation, breaking into one <section> per calendar day with a title frame —
    the counterpart of `process_sheet`'s SECTION/text-frame handling for the case there is
    no sheet to read them from (mirrors `process_dir`'s shape otherwise).
    """
    if not paths:
        raise ValueError("No media files resolved for --people")
    output: list[str] = []
    current_day: str | None = None
    for path in (pbar := tqdm(paths)):
        pbar.set_postfix_str(path.name)
        day = day_of(path)
        pieces = []
        if day != current_day:
            if current_day is not None:
                pieces.append("</section><section>")
            pieces.append(TEMPLATE_TEXT.format(title=day_title(day), text=""))
            current_day = day
        pieces.append(render_media(m, Path(m.env.convert.run(path))))
        if m.env.output:
            output.extend(pieces)
        else:
            for piece in pieces:
                print(piece)
    write_presentation(m, output, m.env.output)


def dump_people_sheet(paths: list[Path], day_of: Callable[[Path], str], out: Path) -> None:
    """Write the same resolved+sectioned --people selection `process_people` would render,
    as an .ods spreadsheet in the `Env.sheet`-documented format instead of HTML, so it can be
    hand-tuned in LibreOffice and then built normally with `build --file`.
    """
    import ezodf

    rows: list[tuple[str, str, str, str]] = []
    current_day: str | None = None
    for path in paths:
        day = day_of(path)
        if day != current_day:
            if current_day is not None:
                rows.append(("SECTION", "", "", ""))
            rows.append(("", "", day_title(day), ""))
            current_day = day
        rows.append(("", str(path), "", ""))

    doc = ezodf.newdoc(doctype="ods", filename=str(out))
    sheet = ezodf.Sheet("people", size=(len(rows) + 1, 4))
    doc.sheets += sheet
    for col, header in enumerate(("comment", "filename", "start", "commands")):
        sheet[0, col].set_value(header)
    for r, row in enumerate(rows, start=1):
        for c, value in enumerate(row):
            if value:
                sheet[r, c].set_value(value)
    doc.save()
    print(f"Written to {out}")


def process_sheet(m, suffix, sheet):
    print(f"Processing: {m.env.file} / {sheet.name}")
    output = []
    for row in (pbar := tqdm(list(sheet.rows())[1:])):
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
            device, gps, dt = None, None, None
            if suff in IMAGE_SUFFIXES:
                if m.env.read_exif:
                    device, gps, dt = read_exif(path) # TODO UPRAV SABLONU

                template = TEMPLATE_IMG
                points = start or ""
                if any(c.strip() for c in commands if c):
                    logger.warning(
                            f"commands are being ignored for img '{filename}' {commands}"
                        )
            else:
                template = TEMPLATE_VIDEO
                if m.env.read_exif:
                    dt = capture_time(path, tools=DEFAULT_TOOLS)
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
                    m.env.filename_autosearch
                    and not path.exists()
                    and is_plain_filename(path)
                ):
                if p := find_file_recursive(filename, m.env.filename_autosearch):
                    path = p
                    filename = str(path)
                else:
                    logger.warning("Filename %s does not exist", filename)

                # convert to cache
            filename_used = m.env.convert.run(path)

            out = template.render(
                points=points,
                src=filename_used,
                datetime=dt.isoformat() if dt else None,
                device=device,
                gps=gps,
            )
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

    fname = m.env.output
    if fname and suffix:
        fname = fname.with_name(f"{fname.stem}_{sheet.name}{fname.suffix}")
    write_presentation(m, output, fname)