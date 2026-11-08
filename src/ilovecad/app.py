# SPDX-License-Identifier: GPL-3.0-or-later
"""Application entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from . import APP_NAME, APP_VERSION
from .fonts import ui_font
from .theme import apply_theme, load_theme
from .ui.main_window import MainWindow

DRAWING_SUFFIXES = (".dwg", ".dxf")


def main(argv: list[str] | None = None) -> int:
    """Start the viewer; the first .dwg/.dxf argument is opened on startup.

    Keeping the file argument out of QApplication's argv avoids Qt treating a
    drawing path as a platform option (and keeps ``python main.py a.dwg``
    usable from a shortcut or a drag-and-drop launch).
    """
    args = list(argv if argv is not None else sys.argv[1:])
    idx = next((i for i, a in enumerate(args)
                if a.lower().endswith(DRAWING_SUFFIXES)), None)
    initial = args[idx] if idx is not None else None
    qt_args = [a for i, a in enumerate(args) if i != idx]
    app = QApplication.instance() or QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("IloveCAD")
    app.setStyle("Fusion")
    app.setFont(ui_font())
    apply_theme(load_theme(), app)
    window = MainWindow()
    window.show()
    if initial and Path(initial).is_file():
        # Let Qt assign the real canvas geometry before fitting the view.
        app.processEvents()
        window.open_file(initial)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
