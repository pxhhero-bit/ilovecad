# SPDX-License-Identifier: GPL-3.0-or-later
"""Application-wide dark / light themes.

Dark is the default. The choice is stored in a plain local INI file via
QSettings: no account, no cloud sync, no telemetry, no network access.
The .ui files intentionally carry no colours so switching is instant.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

DARK = "dark"
LIGHT = "light"
DEFAULT_THEME = DARK
_KEY = "appearance/theme"

_DARK_COLORS = {
    "windowbg": "#1e2025", "panel": "#25282e", "base": "#1a1d22",
    "alt": "#22262d", "fg": "#e6e8ec", "muted": "#a7adb8",
    "border": "#3a4049", "btn": "#2c313a", "hover": "#39414d",
    "pressed": "#454f5c", "tab": "#23262c", "sel": "#2f6bb5",
    "selfg": "#ffffff", "accent": "#5b8fd6", "scroll": "#4a525e",
    "tip": "#1a1d22",
}
_LIGHT_COLORS = {
    "windowbg": "#f5f6f8", "panel": "#f5f6f8", "base": "#ffffff",
    "alt": "#f3f5f8", "fg": "#1f2329", "muted": "#5a6270",
    "border": "#d7dce4", "btn": "#f7f8fa", "hover": "#e8eef8",
    "pressed": "#d9e3f2", "tab": "#eceef2", "sel": "#2f6bb5",
    "selfg": "#ffffff", "accent": "#7aa3d8", "scroll": "#c3cad4",
    "tip": "#ffffff",
}

# Percent-substitution keeps the CSS braces untouched and readable.
_STYLE = """
QMainWindow, QDialog { background:%(windowbg)s; }
QToolButton {
    min-width:74px; padding:4px; border:1px solid %(border)s;
    border-radius:4px; background:%(btn)s; color:%(fg)s;
}
QToolButton:hover { background:%(hover)s; border:1px solid %(accent)s; }
QToolButton:pressed, QToolButton:checked { background:%(pressed)s; }
QTabWidget::pane { border:0; border-top:1px solid %(border)s; background:%(panel)s; }
QTabBar::tab {
    background:%(tab)s; color:%(fg)s; padding:6px 14px;
    border:1px solid %(border)s; border-bottom:0; margin-right:2px;
    border-top-left-radius:4px; border-top-right-radius:4px;
}
QTabBar::tab:selected { background:%(panel)s; }
QTabBar::tab:!selected { margin-top:2px; }
QTreeWidget, QListWidget, QTextBrowser, QPlainTextEdit, QLineEdit {
    background:%(base)s; color:%(fg)s; border:1px solid %(border)s;
    selection-background-color:%(sel)s; selection-color:%(selfg)s;
}
QComboBox, QSpinBox, QDoubleSpinBox {
    background:%(base)s; color:%(fg)s; border:1px solid %(border)s;
    border-radius:3px; padding:3px 8px;
}
QTableWidget, QTableView {
    background:%(base)s; color:%(fg)s; border:1px solid %(border)s;
    gridline-color:%(border)s; alternate-background-color:%(alt)s;
    selection-background-color:%(sel)s; selection-color:%(selfg)s;
}
QTableWidget::item:selected { background:%(sel)s; color:%(selfg)s; }
QTableCornerButton::section { background:%(btn)s; border:0; }
QComboBox::drop-down {
    subcontrol-origin:padding; subcontrol-position:top right;
    width:18px; border:0; border-left:1px solid %(border)s;
}
QComboBox::down-arrow {
    image:none; border-left:4px solid transparent;
    border-right:4px solid transparent; border-top:6px solid %(fg)s;
    margin-right:6px;
}
QComboBox QAbstractItemView {
    background:%(base)s; color:%(fg)s; border:1px solid %(border)s;
    selection-background-color:%(sel)s; selection-color:%(selfg)s;
}
QProgressBar {
    background:%(base)s; color:%(fg)s; border:1px solid %(border)s;
    border-radius:3px; text-align:center; min-height:16px;
}
QProgressBar::chunk { background:%(sel)s; }
QHeaderView::section {
    background:%(btn)s; color:%(fg)s; border:0;
    border-right:1px solid %(border)s; border-bottom:1px solid %(border)s;
    padding:4px;
}
QFrame#commandBar { background:%(panel)s; border:1px solid %(border)s; border-radius:3px; }
QStatusBar { background:%(panel)s; color:%(muted)s; }
QStatusBar QLabel { color:%(muted)s; }
QMenuBar { background:%(panel)s; color:%(fg)s; }
QMenuBar::item:selected { background:%(hover)s; }
QMenu { background:%(base)s; color:%(fg)s; border:1px solid %(border)s; }
QMenu::item:selected { background:%(sel)s; color:%(selfg)s; }
QPushButton {
    background:%(btn)s; color:%(fg)s; border:1px solid %(border)s;
    border-radius:3px; padding:4px 10px;
}
QPushButton:hover { background:%(hover)s; }
QPushButton:disabled, QToolButton:disabled {
    color:%(muted)s; background:%(panel)s; border:1px solid %(border)s;
}
QGroupBox {
    color:%(fg)s; border:1px solid %(border)s; border-radius:4px;
    margin-top:18px; padding-top:12px;
}
QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }
QRadioButton, QCheckBox { color:%(fg)s; background:transparent; }
QSplitter::handle { background:%(border)s; }
QSplitter::handle:horizontal { width:4px; }
QSplitter::handle:vertical { height:4px; }
QDialogButtonBox QPushButton { min-width:76px; }
QScrollBar:vertical { background:%(panel)s; width:10px; margin:0; }
QScrollBar::handle:vertical { background:%(scroll)s; border-radius:5px; min-height:24px; }
QScrollBar:horizontal { background:%(panel)s; height:10px; margin:0; }
QScrollBar::handle:horizontal { background:%(scroll)s; border-radius:5px; min-width:24px; }
QToolTip { background:%(tip)s; color:%(fg)s; border:1px solid %(border)s; }
"""


def normalize(value) -> str:
    """Anything unknown falls back to the dark default."""
    if str(value).strip().lower() in (LIGHT, "浅色", "0"):
        return LIGHT
    return DARK


def is_dark(theme: str) -> bool:
    return normalize(theme) == DARK


def label(theme: str) -> str:
    return "深色主题（黑底）" if is_dark(theme) else "浅色主题（白底）"


def _settings() -> QSettings:
    # IniFormat + UserScope keeps the app portable (no registry / no cloud).
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                     "IloveCAD", "IloveCAD")


def load_theme() -> str:
    try:
        return normalize(_settings().value(_KEY, DEFAULT_THEME))
    except Exception:
        return DEFAULT_THEME


def save_theme(theme: str) -> str:
    value = normalize(theme)
    try:
        s = _settings()
        s.setValue(_KEY, value)
        s.sync()
    except Exception:
        pass  # A read-only home directory must never break the viewer.
    return value


def _dark_palette() -> QPalette:
    c = _DARK_COLORS
    p = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    pairs = {
        role.Window: c["panel"], role.WindowText: c["fg"],
        role.Base: c["base"], role.AlternateBase: c["alt"],
        role.Text: c["fg"], role.Button: c["btn"],
        role.ButtonText: c["fg"], role.ToolTipBase: c["tip"],
        role.ToolTipText: c["fg"], role.Highlight: c["sel"],
        role.HighlightedText: c["selfg"], role.Link: c["accent"],
        role.PlaceholderText: c["muted"],
    }
    for r, hexcolor in pairs.items():
        p.setColor(r, QColor(hexcolor))
    for r in (role.WindowText, role.Text, role.ButtonText):
        p.setColor(group.Disabled, r, QColor(c["muted"]))
    return p


def apply_theme(theme: str, app: QApplication | None = None) -> str:
    """Apply the palette and stylesheet to the whole application. Idempotent."""
    value = normalize(theme)
    app = app or QApplication.instance()
    if app is None:
        return value
    if value == DARK:
        app.setPalette(_dark_palette())
        colors = _DARK_COLORS
    else:
        app.setPalette(app.style().standardPalette())
        colors = _LIGHT_COLORS
    app.setStyleSheet(_STYLE % colors)
    return value
