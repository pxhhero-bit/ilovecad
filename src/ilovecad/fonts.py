# SPDX-License-Identifier: GPL-3.0-or-later
"""Central CJK-aware font fallback policy.

A comma-separated string such as "Microsoft YaHei, Segoe UI, sans-serif"
is NOT a font list in Qt: QFont treats it as one literal family name and
silently falls back to a font that often has no CJK glyphs (rendered as
empty boxes). Qt's real fallback mechanism is QFont.setFamilies([...]).
"""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

FALLBACK_FAMILIES = [
    "Microsoft YaHei", "微软雅黑", "Microsoft JhengHei", "SimHei",
    "SimSun", "Noto Sans CJK SC", "Source Han Sans SC",
    "Segoe UI", "Arial", "sans-serif",
]


def _families() -> list[str]:
    available = set(QFontDatabase.families())
    chosen = [f for f in FALLBACK_FAMILIES if f in available]
    # Keep the whole list when the platform reports none (e.g. Qt offscreen
    # on Windows registers no font database at all) so a real GUI session
    # can still resolve the names.
    return chosen or FALLBACK_FAMILIES


def ui_font(point_size: float = 9) -> QFont:
    font = QFont()
    font.setFamilies(_families())
    font.setPointSizeF(point_size)
    return font


def drawing_font(point_size: float = 10, pixel_size: int | None = None) -> QFont:
    font = QFont()
    font.setFamilies(_families())
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    else:
        font.setPointSizeF(point_size)
    return font
