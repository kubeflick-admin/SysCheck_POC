"""SysCheck POC — application entry point.

Run from source:
    python main.py

See ``build.ps1`` / README for the frozen single-file .exe build.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Import Qt lazily so a missing dependency gives a clean message rather
    # than a traceback at module import time.
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont

    from syscheck import config
    from syscheck.ui.theme import build_stylesheet
    from syscheck.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SysCheck")
    app.setFont(QFont(config.MONO_FONT, 10))
    app.setStyleSheet(build_stylesheet())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    # freeze_support keeps any future multiprocessing use safe under
    # PyInstaller's bootloader.
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
