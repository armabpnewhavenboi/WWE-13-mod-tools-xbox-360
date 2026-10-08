"""Entry point used for the Windows .exe builds (PyInstaller)."""

import os
import sys

if sys.stdout is None:  # windowed build started with arguments: no console to print to
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from x360resign.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
