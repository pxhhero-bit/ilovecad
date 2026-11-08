# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen rendering path: DisplayList -> QPainter on the canvas widget."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPolygonF

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
        if not item.text:
            continue
        pos = view.to_screen(item.pos)
        size = max(7.0, min(96.0, item.height * view.scale))
        font = drawing_font(size)
        painter.setFont(font)
        r, g, b = item.color
        painter.setPen(QColor(230, 90, 40) if item.annotation else QColor(r, g, b))
        painter.save()
        painter.translate(QPointF(pos[0], pos[1]))
        if item.rotation:
            painter.rotate(-item.rotation)
        painter.drawText(0, 0, item.text.replace("\n", " "))
        painter.restore()
