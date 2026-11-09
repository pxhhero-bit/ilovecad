# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen rendering path: DisplayList -> QPainter on the canvas widget."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPen, QPolygonF

from .displaylist import DisplayList
from ..fonts import drawing_font
from .view import ViewTransform


def paint_display_list(painter, dl: DisplayList, view: ViewTransform,
                       show_lineweight: bool = True) -> None:
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    for path in dl.paths:
        if len(path.points) < 2:
            continue
        r, g, b = path.color
        color = QColor(245, 140, 30) if path.selected else QColor(r, g, b)
        if path.annotation:
            color = QColor(220, 60, 40)
        px_width = (max(0.7, min(4.5, path.width * view.scale))
                    if show_lineweight and path.width else 1.0)
        if path.annotation:
            px_width = max(px_width, 1.4)
        pen = QPen(color)
        pen.setWidthF(px_width)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.DashLine if path.dashed else Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        poly = QPolygonF([QPointF(*view.to_screen(p)) for p in path.points])
        if path.filled:
            brush = QBrush(color)
            painter.setBrush(brush)
            painter.drawPolygon(poly, Qt.FillRule.OddEvenFill)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        elif path.closed:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPolygon(poly)
        else:
            painter.drawPolyline(poly)

    for item in dl.texts:
        lines = item.lines or [str(item.text).replace("\n", " ")]
        if not lines or not any(lines) or item.height <= 0:
            continue
        pos = view.to_screen(item.pos)
        # Keep text proportional to the geometry; the optional viewer-side
        # cap only compresses pathologically large titles and preserves order.
        size = max(6.0, min(512.0, item.height * view.scale))
        font = drawing_font(size, families=item.families or None,
                            bold=item.bold, italic=item.italic,
                            underline=item.underline,
                            strikeout=item.strikeout)
        painter.setFont(font)
        fm = QFontMetrics(font)
        r, g, b = item.color
        painter.setPen(QColor(230, 90, 40) if item.annotation else QColor(r, g, b))
        line_h = size * 1.25 * item.line_spacing
        n = len(lines)
        hpos = (item.align - 1) % 3
        vpos = (item.align - 1) // 3
        if n > 1:
            if vpos == 0:
                y0 = size * 0.85
            elif vpos == 1:
                y0 = -(n - 1) * line_h / 2.0
            else:
                y0 = -(n - 1) * line_h - size * 0.15
        else:
            y0 = 0.0
        for i, line in enumerate(lines):
            w = fm.horizontalAdvance(line)
            x = -(w / 2.0 if hpos == 1 else w if hpos == 2 else 0.0)
            painter.save()
            painter.translate(QPointF(pos[0], pos[1]))
            if item.rotation:
                painter.rotate(-item.rotation)
            if abs(item.width_scale - 1.0) > 1e-3:
                painter.scale(item.width_scale, 1.0)
            if abs(item.oblique) > 0.5:
                painter.shear(math.tan(math.radians(item.oblique)), 0.0)
            painter.drawText(QPointF(x, y0 + i * line_h), line)
            painter.restore()
