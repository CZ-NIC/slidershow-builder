#!/usr/bin/env python3
import logging

import ezodf
from mininterface import run

from ._lib.env import Env
from ._lib.find_file_recursive import filename_cache
from ._lib.process import process_sheet

logger = logging.getLogger(__name__)


def main():
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

    with filename_cache(m.env.filename_autosearch_cache):
        for sheet in sheets:
            process_sheet(m, suffix, sheet)


if __name__ == "__main__":
    main()
