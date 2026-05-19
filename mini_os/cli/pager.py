from __future__ import annotations

import os
import shutil
import subprocess
import sys


def page(text: str, enabled: bool = True) -> None:
    if not text:
        return
    height = shutil.get_terminal_size((80, 24)).lines
    if not enabled or not sys.stdout.isatty() or text.count("\n") < height - 2:
        print(text)
        return
    pager = os.environ.get("PAGER")
    if pager:
        proc = subprocess.Popen([pager], stdin=subprocess.PIPE, text=True)
        proc.communicate(text)
        return
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        print(line)
        if idx % (height - 1) == 0:
            try:
                input("-- more --")
            except EOFError:
                break
