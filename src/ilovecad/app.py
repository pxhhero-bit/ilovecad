# SPDX-License-Identifier: GPL-3.0-or-later
"""Application entry point."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_VERSION
from .fonts import ui_font
from .ui.main_window import MainWindow


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    app = QApplication.instance() or QApplication([sys.argv[0]] + args)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("IloveCAD")
    app.setStyle("Fusion")
    app.setFont(ui_font())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
