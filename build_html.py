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
# Commands
#   * Xposouvací šipka, ex: `48→60.5` skočí z času 48 s na 60.5
#   * číslo značí start point momentu, jehož akce určují následující buňky
#   * posouvací šipka, ex: `→60.5` skočí z momentu na 60.5
#   * rate a číslo změní rate momentu `rate 2`
#   * Xdata-video-point, akorát tokeny nemusí být v uvozovkách ex: `110,rate:.5`
#   * poslední osamělé číslo je end


@dataclass
class Env:
    sheet: Path
    output: Path | None = None
    template = Path("skelet.html.template")


TEMPLATE = """<article data-video-points='[{points}]'><video controls="controls" data-src="{src}"></video></article>"""
NUM = r"(\d+(?:\.\d+)?)"  # a number including decimal


def output_tokens(moment, tokens):
    return f'[{moment}, {",".join(f'"{t}"' for t in tokens)}]'


def parse_commands(start: str | None, commands: list[str]):
    moment = None
    tokens = []

    if start and start != '0':
        commands.insert(0, "0")
        commands.insert(1, f"→{start}")

    commands = [r for r in commands if r is not None]  # filter out trailing empty cells
    logger.info(commands)
    for i, command in enumerate(commands):
        if m := match(rf"{NUM}$", command):
            if moment or tokens:
                if not tokens:
                    raise ValueError(f"No action at {command} at: {commands}")
                if not moment:
                    moment = "0"
                yield output_tokens(moment, tokens)

            moment = m[0]
            tokens.clear()
            if i == len(commands)-1:  # end number, stop at this moment1
                yield f'[{m[0]}, "pause"]'
            continue
        elif m := match(rf"→{NUM}$", command):
            tokens.append(f"goto:{m[1]}")
        elif m := match(rf"{NUM}→{NUM}", command):
            if moment:
                raise ValueError(f"Moment already defined, moment {moment} while processing {command} at {commands}")
            yield f'[{m[1]}, "goto:{m[2]}"]'
        elif m := match(rf"rate\s{NUM}", command):
            tokens.append(f"rate:{m[1]}")
        elif command in ("mute", "unmute"):
            tokens.append(command)
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
            quit()
        out = TEMPLATE.format(points=",".join(parse_commands(start, commands)), src=filename)
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
