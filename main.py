from __future__ import annotations

from mini_os.cli.shell import main

import os

os.system("cls" if os.name == "nt" else "clear")

if __name__ == "__main__":
    raise SystemExit(main())
