# SPDX-License-Identifier: GPL-3.0-or-later
"""Screen rendering path: DisplayList -> QPainter on the canvas widget."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPen, QPolygonF

from .displaylist import DisplayList
from ..fonts import drawing_font
from ..model import geometry as geo
from .view import ViewTransform


def paint_display_list(painter, dl: DisplayList, view: ViewTransform,
                       show_lineweight: bool = True, *,
                       cull: bool = True,
                       fade_selection: bool = False) -> None:
    """Draw the static scene.

    ``cull`` skips strokes wholly outside the viewport and decimates extremely
    dense polylines whose screen footprint is smaller than a pixel per vertex.
    ``fade_selection`` dims the selected source objects while a transform
    preview shows where they *will* land; the preview itself is drawn by the
    canvas as a dynamic overlay, so it never enters the cached scene pixmap.
    """
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    vx0, vy0, vx1, vy1 = view.visible_bounds() if cull else (0, 0, 0, 0)
    pad = 8.0 / max(view.scale, 1e-9)
    vx0, vy0, vx1, vy1 = vx0 - pad, vy0 - pad, vx1 + pad, vy1 + pad

    for path in dl.paths:
        if len(path.points) < 2:
            continue
        if cull and path.bounds:
            b = path.bounds
            if b[2] < vx0 or b[0] > vx1 or b[3] < vy0 or b[1] > vy1:
                continue
        r, g, b = path.color
        if path.selected and fade_selection:
            # Dimmed *source* geometry: keep its own colour, lower the alpha.
            color = QColor(r, g, b, 62)
        elif path.selected:
            color = QColor(245, 140, 30)
        else:
            color = QColor(r, g, b)
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
        pts = path.points
        if cull and len(pts) > 96:
            # LOD: a 900-point spline fitted into 30 screen pixels does not
            # need 900 QPointF allocations on every zoom frame.
            span = max((path.bounds[2] - path.bounds[0]) * view.scale,
                       (path.bounds[3] - path.bounds[1]) * view.scale) \
                if path.bounds else 0.0
            if span > 0 and span / len(pts) < 0.6:
                step = max(1, int(len(pts) / max(span, 1.0)))
                pts = pts[::step] + ([pts[-1]] if pts[-1] != pts[::step][-1] else [])
        poly = QPolygonF([QPointF(*view.to_screen(p)) for p in pts])
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
        size = max(6.0, min(512.0, item.height * view.scale))
        # Sub-pixel glyphs cost a full font shaping pass each; nothing readable
        # is lost by skipping them until the user zooms in.
        if item.height * view.scale < 2.0:
            continue
        if cull and (pos[0] < -400 or pos[0] > view.width + 400 or
                     pos[1] < -400 or pos[1] > view.height + 400):
            continue
        font = drawing_font(size, families=item.families or None,
                            bold=item.bold, italic=item.italic,
                            underline=item.underline,
                            strikeout=item.strikeout)
        painter.setFont(font)
        fm = QFontMetrics(font)
        r, g, b = item.color
        if item.selected and fade_selection:
            painter.setPen(QColor(r, g, b, 62))
        elif item.annotation:
            painter.setPen(QColor(230, 90, 40))
        else:
            painter.setPen(QColor(r, g, b))
        offsets = geo.text_baselines(len(lines), size, fm.ascent(), fm.descent(),
                                   item.line_spacing, item.align,
                                   item.baseline)
        hpos = (item.align - 1) % 3
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
            painter.drawText(QPointF(x, offsets[i]), line)
            painter.restore()
