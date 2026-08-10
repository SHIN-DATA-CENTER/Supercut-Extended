"""PySide6 desktop UI for Supercut Extended."""

from __future__ import annotations

import sys


APP_ID = "ShinDataCenter.SupercutExtended"


def _claim_taskbar_identity() -> None:
    """Give Windows an explicit AppUserModelID so the taskbar uses our icon.

    Without this, a process launched as `python supercut.py` is grouped under
    python.exe and the taskbar shows the Python icon no matter what the window icon
    is. Purely cosmetic, and unavailable off Windows, so failure is ignored.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from . import icons
    from .main_window import MainWindow

    _claim_taskbar_identity()

    app = QApplication(sys.argv)
    app.setApplicationName("Supercut Extended")
    # Application-wide so dialogs and message boxes inherit it too.
    app.setWindowIcon(icons.app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
