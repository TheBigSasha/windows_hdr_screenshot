"""PyInstaller entry point.

A frozen build launches the GUI by default; passing CLI args (``info``, ``full``,
``capture``, …) runs the command-line interface, so one bundled exe serves both.
"""
import sys

from hdrshot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
