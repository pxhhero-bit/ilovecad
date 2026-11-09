# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent PDF rendering path.

This module paints the unified DisplayList straight onto Qt's vector PDF
paint device. It never calls LibreDWG, never writes a DWG, never rasterises
the screen, and is completely decoupled from the AC1018 version gate.
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFontMetrics, QPageLayout, QPageSize,
                           QPainter, QPdfWriter, QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import QApplication

from ..model import geometry as g
from ..model.document import DwgDocument
from .displaylist import DisplayList
from ..fonts import drawing_font

PAPER_IDS = {"A4": QPageSize.A4, "A3": QPageSize.A3, "A2": QPageSize.A2}

# Qt's font database needs a QApplication; drawing text without one can abort
# the process. When this independent path is called from a CLI/test with no
# GUI running, create an offscreen application and keep it alive.
_PDF_APP = None


def _ensure_qt_app() -> None:
    global _PDF_APP
    if QApplication.instance() is not None:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _PDF_APP = QApplication([sys.argv[0] if sys.argv else "ilovecad-pdf"])


@dataclass
class PdfOptions:
    paper: str = "A3"
    landscape: bool = True
    scope: str = "全图"          # 全图 | 当前视图 | 完整范围
    scale_denominator: int = 100
    monochrome: bool = False
    include_measurements: bool = True
    use_lineweight: bool = True


class PdfRenderer:
    """Vector PDF output, independent of the DWG write path."""

    @staticmethod
    def export(path: str, doc: DwgDocument, dl: DisplayList,
               options: PdfOptions, view_bounds=None) -> dict:
        _ensure_qt_app()
        out_path = str(path)
        writer = QPdfWriter(path)
        writer.setResolution(300)
        writer.setTitle("IloveCAD - 2D drawing")
        writer.setCreator("IloveCAD (GPL-3.0-or-later), independent vector PDF path")
        writer.setPageSize(QPageSize(PAPER_IDS.get(options.paper, QPageSize.A3)))
        writer.setPageOrientation(
            QPageLayout.Landscape if options.landscape else QPageLayout.Portrait)
        writer.setPageMargins(QMarginsF(0, 0, 0, 0))

        painter = QPainter(writer)
        if not painter.isActive():
            raise RuntimeError("无法启动 PDF 绘制设备")

        dpi = writer.resolution()
        pw, ph = writer.width(), writer.height()
        margin = 10.0 / 25.4 * dpi
        avail_w, avail_h = max(pw - 2 * margin, 1), max(ph - 2 * margin, 1)
        b = view_bounds if options.scope == "当前视图" and view_bounds else None
        if b is None:
            b = (dl.bounds if str(options.scope).startswith("完整范围") else
                 (dl.fit_bounds or dl.bounds))
        if not b:
            b = (0.0, 0.0, 100.0, 100.0)
        model_w = max(b[2] - b[0], 1e-6)
        model_h = max(b[3] - b[1], 1e-6)
        fit = min(avail_w / model_w, avail_h / model_h)
        unit_mm = g.UNIT_MM.get(doc.units, 1.0)
        px_per_mm = dpi / 25.4
        requested = px_per_mm * unit_mm / max(options.scale_denominator, 1)
        s = min(fit, requested) if requested > 0 else fit
        s = max(s, 1e-9)

        left = margin + (avail_w - model_w * s) / 2.0
        top = margin + (avail_h - model_h * s) / 2.0
        tx = left - b[0] * s
        ty = top + b[3] * s
        transform = QTransform(s, 0, 0, -s, tx, ty)
        painter.setWorldTransform(transform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if options.scope == "当前视图" and view_bounds:
            painter.setClipRect(QRectF(view_bounds[0], view_bounds[1],
                                       view_bounds[2] - view_bounds[0],
                                       view_bounds[3] - view_bounds[1]))

        for path in dl.paths:
            if len(path.points) < 2:
                continue
            r, gg, bb = path.color
            color = QColor(220, 60, 40) if path.annotation else (
                QColor(0, 0, 0) if options.monochrome else QColor(r, gg, bb))
            width_model = (max(path.width, 0.15 / s)
                           if options.use_lineweight else 0.25 / s)
            pen = QPen(color)
            pen.setWidthF(width_model)
            pen.setCosmetic(False)
            pen.setStyle(Qt.PenStyle.DashLine if path.dashed else Qt.PenStyle.SolidLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            poly = QPolygonF([QPointF(p[0], p[1]) for p in path.points])
            if path.filled:
                painter.setBrush(QBrush(color))
                painter.drawPolygon(poly, Qt.FillRule.OddEvenFill)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            elif path.closed:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolygon(poly)
            else:
                painter.drawPolyline(poly)

        # Text is drawn untransformed so the y-axis flip cannot mirror glyphs.
        painter.setWorldTransform(QTransform())
        for item in dl.texts:
            if not item.text or item.height <= 0:
                continue
            lines = item.lines or [str(item.text).replace("\n", " ")]
            if not lines or not any(lines):
                continue
            dev = transform.map(QPointF(item.pos[0], item.pos[1]))
            size_px = max(4, min(800, int(item.height * s)))
            font = drawing_font(pixel_size=size_px,
                                families=item.families or None,
                                bold=item.bold, italic=item.italic,
                                underline=item.underline,
                                strikeout=item.strikeout)
            painter.setFont(font)
            fm = QFontMetrics(font)
            r, gg, bb = item.color
            painter.setPen(QColor(0, 0, 0) if options.monochrome else
                           (QColor(220, 60, 40) if item.annotation else QColor(r, gg, bb)))
            line_h = size_px * 1.25 * item.line_spacing
            n = len(lines)
            hpos = (item.align - 1) % 3
            vpos = (item.align - 1) // 3
            if n > 1:
                if vpos == 0:
                    y0 = size_px * 0.85
                elif vpos == 1:
                    y0 = -(n - 1) * line_h / 2.0
                else:
                    y0 = -(n - 1) * line_h - size_px * 0.15
            else:
                y0 = 0.0
            for i, line in enumerate(lines):
                w = fm.horizontalAdvance(line)
                x = -(w / 2.0 if hpos == 1 else w if hpos == 2 else 0.0)
                painter.save()
                painter.translate(dev)
                if item.rotation:
                    painter.rotate(-item.rotation)
                if abs(item.width_scale - 1.0) > 1e-3:
                    painter.scale(item.width_scale, 1.0)
                if abs(item.oblique) > 0.5:
                    painter.shear(math.tan(math.radians(item.oblique)), 0.0)
                painter.drawText(QPointF(x, y0 + i * line_h), line)
                painter.restore()

        painter.end()
        effective = (px_per_mm * unit_mm) / s if s else 0.0
        return {
            "path": out_path,
            "page_px": (pw, ph),
            "dpi": dpi,
            "scale": s,
            "effective_denominator": int(math.ceil(effective)),
            "requested_denominator": options.scale_denominator,
            "clamped_to_fit": requested > fit,
        }
