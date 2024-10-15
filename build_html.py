#!/usr/bin/env python3
from dataclasses import dataclass
from pathlib import Path
from re import match
import ipdb
from mininterface import run
import ezodf
import logging
logger = logging.getLogger(__name__)

# Format of the xls
# comment   filename	start	commands
# filenam
#   * když prázdné, tak start se vezme jako text
# Commands
#   * číslo značí start point momentu, jehož akce určují následující buňky
#   * posouvací šipka, ex: `→60.5` skočí z momentu na 60.5
#   * rate a číslo změní rate momentu `rate 2`
#   * mute, unmute
#   * čárka se bere jako nezávislé commandy `rate 2, unmute` → `rate 2` a `unmute`
#   * poslední osamělé číslo je end


@dataclass
class Env:
    sheet: Path
    output: Path | None = None
    template = Path("skelet.html.template")


TEMPLATE = """<article data-video-points='[{points}]'><video controls="controls" data-src="{src}"></video></article>"""
TEMPLATE_TEXT = """<article class="main">
                    <h1>{title}</h1>
                    <p>{text}</p>
                </article>"""
NUM = r"(\d+(?::\d+)?(?:\.\d+)?)"  # a number including decimal and colon

clist = {"mute": "mute", "unmute": "unmute", "M": "mute", "U": "unmute"}


def output_tokens(moment, tokens):
    return f'[{moment}, {",".join(f'"{t}"' for t in tokens)}]'


def num(v: str) -> int:
    """Convert number in format min:sec to total seconds. `1:32.2` → 92.2, `5` → 5 """
    if not ":" in v:
        return v
    minutes, seconds = map(float, v.split(':'))
    total_seconds = minutes * 60 + seconds
    return total_seconds


def parse_commands(start: str | None, commands: list[str]):
    moment: str | float | int | None = None
    tokens = []

    if start and start != '0':
        commands.insert(0, "0")
        commands.insert(1, f"→{num(start)}")

    # filter out trailing empty cells
    # What does this do?
    # `rate1,M` → `rate1` , `M`
    # `point:[161.2,204.9,5]` stays the same
    commands = [r.strip() for subcommand in commands if subcommand is not None for r in (
        [subcommand] if "[" in subcommand else subcommand.split(","))]
    logger.info(commands)
    for i, command in enumerate(commands):
        if m := match(rf"{NUM}$", command):
            if moment or tokens:
                if not tokens:
                    raise ValueError(f"No action at {command} at: {commands}")
                if not moment:
                    moment = "0"
                yield output_tokens(moment, tokens)

            moment = num(m[0])
            tokens.clear()
            if i == len(commands)-1:  # end number, stop at this moment1
                yield f'[{num(m[0])}, "pause"]'
            continue
        elif m := match(rf"→{NUM}$", command):
            tokens.append(f"goto:{num(m[1])}")
        elif m := match(rf"{NUM}→{NUM}", command):
            if moment:
                raise ValueError(f"Moment already defined, moment {moment} while processing {command} at {commands}")
            yield f'[{num(m[1])}, "goto:{num(m[2])}"]'
        elif m := match(rf"R{NUM}(M|U)", command):
            tokens.append(f"rate:{num(m[1])}")
            tokens.append(clist[m[2]])
        elif m := match(rf"(?:rate|R)\s?{NUM}", command):
            tokens.append(f"rate:{num(m[1])}")
        elif command in clist:
            tokens.append(clist[command])
        elif command.startswith("point"):
            tokens.append(command)
        elif m := match(rf"P", command):  # as play
            tokens.append(f"rate:1")
            tokens.append("unmute")
        else:
            raise ValueError(f"Unknown command {command} at {commands}")
    if tokens:
        yield output_tokens(moment, tokens)


def cell_value(val):
    if val is None:
        return val
    if isinstance(val, (int, float)):
        return str(val).replace(".0", "")
    return str(val)


if __name__ == "__main__":
    m = run(Env)
    sheet = ezodf.opendoc(m.env.sheet).sheets[0]
    output = []
    for row in list(sheet.rows())[1:]:
        try:
            comment, filename, start, *commands = [cell_value(cell.value) for cell in row]
        except ValueError:
            print("EARLY STOP")
            break

        if filename:  # video frame
            out = TEMPLATE.format(points=",".join(parse_commands(start, commands)), src=filename)
        elif start or commands:  # text frame
            out = TEMPLATE_TEXT.format(title=start, text="".join(str(a) for a in commands if a))
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

    if m.env.output:
        m.env.output.write_text(m.env.template.read_text().format(contents="\n".join(output)))
