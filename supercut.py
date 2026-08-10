#!/usr/bin/env python
"""Launcher: `python supercut.py` opens the GUI, `python supercut.py <cmd>` runs the CLI."""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in {"gui", "--gui"}:
        from supercut_extended.gui import main as gui_main
        return gui_main()
    from supercut_extended.cli import main as cli_main
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
