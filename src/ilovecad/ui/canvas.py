# SPDX-License-Identifier: GPL-3.0-or-later
"""The 2D drawing canvas: view, snapping, measuring and lightweight editing."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QInputDialog, QMessageBox, QWidget
from ..fonts import ui_font
from ..preferences import load_snap_config

from ..model import geometry as g
from ..model.document import DwgDocument
from ..model.entities import Entity
from ..render.displaylist import build
from ..render.qt_paint import paint_display_list
from ..render.view import ViewTransform
from ..tools import measure
from ..tools.commands import UndoStack
from ..tools import snapping

Point = tuple[float, float]

MODE_HINTS = {
    "select": "选择：单击选中实体，拖动框选；Delete 删除选中对象。",
    "pan": "平移：按住左键拖动；滚轮缩放；Esc 返回选择。",
    "zoom_window": "窗口缩放：拖出一个矩形，放大到该范围。",
    "distance": "测距离：依次点取两个点（对象捕捉自动生效）。",
    "chain": "累计长度：连续点取折线点，双击或回车结束。",
    "area": "测面积：点取闭合多边形顶点，双击或回车结束。",
    "radius": "半径 / 直径：点取圆或圆弧。",
    "angle": "测夹角（三点）：依次点取顶点、第一条边上的点、第二条边上的点。",
    "angle_lines": "测夹角（两线）：依次点取两条直线 / 多段线，自动求较小夹角与补角。",
    "draw_line": "直线：点取起点和终点。",
    "draw_polyline": "多段线：连续点取顶点，双击或回车结束。",
    "draw_rect": "矩形：点取两个对角点。",
    "draw_circle": "圆：点取圆心，再点取半径点。",
    "draw_arc": "圆弧：点取圆心、起点、终点（三点）。",
    "draw_text": "文字：点取插入位置并输入内容。",
    "move": "移动：先选中对象，再点取基点和目标点。",
    "copy": "复制：先选中对象，再点取基点和目标点。",
    "rotate": "旋转：先选中对象，再点取基点、参考点、目标点。",
    "mirror": "镜像：先选中对象，再点取镜像轴上的两点，可选择保留原对象。",
    "scale": "缩放：先选中对象，再点取基点并输入比例（例如 2 或 0.5）。",
    "delete": "删除：删除当前选中的对象。",
}

# Modes that mutate DwgDocument. Measuring, snapping, selection and view
# changes are read-only operations and stay available in a viewer session.
EDIT_MODES = frozenset({
    "draw_line", "draw_polyline", "draw_rect", "draw_circle", "draw_arc",
    "draw_text", "move", "copy", "rotate", "mirror", "scale", "delete",
})


class CadCanvas(QWidget):
    statusMessage = Signal(str)
    coordinateMessage = Signal(str)
    hintMessage = Signal(str)
    measurementAdded = Signal(dict)
    selectionChanged = Signal(int)
    documentEdited = Signal()
    snapStateChanged = Signal(bool)
    snapHint = Signal(str)
    editBlocked = Signal(str)          # a write attempt hit the read-only gate

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.doc = DwgDocument()
        self.undo_stack = UndoStack(self.doc)
        self.view = ViewTransform()
        self.mode = "select"
        # A read-only session must be opened explicitly by the window; a blank
        # unsaved document stays editable (there is no source to protect).
        self.read_only = False
        self.current_layer = "0"
        self.points: list[Point] = []
        self._angle_entities: list[Entity] = []
        self.cursor_model: Point | None = None
        self.cursor_screen: QPointF | None = None
        self.snap_result: snapping.SnapResult | None = None
        self.dark = False
        self.snap_config = load_snap_config()
        self._last_snap_kind: str | None = None
        self.ortho = False
        self.grid = True
        self.show_lineweight = True
        self.include_measurements = True
        self._revision = 0
        self._dl = None
        self._dl_key = None
        self._highlight_index: int | None = None
        self._drag_origin: QPoint | None = None
        self._drag_last: QPoint | None = None
        self._rubber: tuple[QPointF, QPointF] | None = None
        self._panning = False
        self._window_selecting = False
        self.statusMessage.emit("就绪")

    # ------------------------------------------------------------------ state
    def set_document(self, doc: DwgDocument) -> None:
        self.doc = doc
        self.undo_stack.set_document(doc)
        self.points.clear()
        self._angle_entities.clear()
        self.snap_result = None
        self._last_snap_kind = None
        self._highlight_index = None
        self.snapHint.emit("")
        self.mode = "select"
        self.current_layer = "0" if "0" in doc.layers else next(iter(doc.layers), "0")
        self.view.fit(doc.robust_bounds()[0])
        self._revision += 1
        self._emit_hint()
        self.update()

    def invalidate(self) -> None:
        self._revision += 1
        self.update()

    def refresh(self) -> None:
        self._revision += 1
        self.update()
        self.documentEdited.emit()

    def set_read_only(self, value: bool) -> bool:
        """Switch the hard write gate; entering read-only cancels any command."""
        self.read_only = bool(value)
        if self.read_only:
            self.mode = "select"
            self.points.clear()
            self._angle_entities.clear()
            self._rubber = None
            self.undo_stack.clear()
            self._emit_hint()
        self.update()
        return self.read_only

    def _block_edit(self, what: str) -> None:
        self.editBlocked.emit(what)
        self.statusMessage.emit(
            "只读会话：看图 / 测量 / PDF 可用；如需编辑请输入 EDIT 或点"
            "「进入编辑」，确认降级风险后才启用写入管线")

    def _reset_to_select(self) -> None:
        self.mode = "select"
        self.points.clear()
        self._angle_entities.clear()
        self._rubber = None
        self._emit_hint()
        self.update()

    def set_mode(self, mode: str) -> bool:
        if mode not in MODE_HINTS:
            return False
        if self.read_only and mode in EDIT_MODES:
            self._block_edit(mode)
            return False
        self.mode = mode
        self.points.clear()
        self._angle_entities.clear()
        self._rubber = None
        if mode == "delete":
            self.delete_selected()
            self.set_mode("select")
            return True
        self._emit_hint()
        self.update()
        return True

    def _emit_hint(self) -> None:
        self.hintMessage.emit(MODE_HINTS.get(self.mode, "就绪"))
        self.statusMessage.emit(f"当前命令：{self.mode}")

    # ----------------------------------------------------------- view controls
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view.width, self.view.height = self.width(), self.height()
        self.update()

    def fit_view(self, full: bool = False) -> None:
        if full:
            self.view.fit(self.doc.bounds())
            self.statusMessage.emit("已按完整范围显示（包含离群对象）")
        else:
            bounds, ignored = self.doc.robust_bounds()
            self.view.fit(bounds)
            if ignored:
                self.statusMessage.emit(
                    f"全图视图已按主体图幅显示，忽略 {ignored} 个远处独立图框 / "
                    "离群对象；输入 FITALL 查看完整范围，PDF 可选「完整范围」")
        self.update()

    def zoom_in(self) -> None:
        self.view.zoom(1.25)
        self.update()

    def zoom_out(self) -> None:
        self.view.zoom(0.8)
        self.update()

    def set_dark(self, value: bool) -> bool:
        """Set the canvas background explicitly (driven by the app theme)."""
        self.dark = bool(value)
        self.invalidate()
        return self.dark

    def toggle_dark(self) -> bool:
        return self.set_dark(not self.dark)

    # ------------------------------------------------------------- snapping
    @property
    def snap_enabled(self) -> bool:
        return self.snap_config.enabled

    @snap_enabled.setter
    def snap_enabled(self, value: bool) -> None:
        self.snap_config.enabled = bool(value)

    def set_snap_config(self, config: snapping.SnapConfig) -> None:
        """Apply a typed snap configuration coming from the settings dialog."""
        self.snap_config = config.copy()
        self.snap_result = None
        self._last_snap_kind = None
        self.snapHint.emit("")
        self.snapStateChanged.emit(self.snap_config.enabled)
        self.invalidate()

    def set_snap_enabled(self, value: bool) -> bool:
        self.snap_config.enabled = bool(value)
        self.snapStateChanged.emit(self.snap_config.enabled)
        return self.snap_config.enabled

    def toggle_snap(self) -> bool:
        return self.set_snap_enabled(not self.snap_config.enabled)

    def toggle_ortho(self) -> bool:
        self.ortho = not self.ortho
        return self.ortho

    def toggle_grid(self) -> bool:
        self.grid = not self.grid
        self.invalidate()
        return self.grid

    def toggle_lineweight(self) -> bool:
        self.show_lineweight = not self.show_lineweight
        self.invalidate()
        return self.show_lineweight

    def set_highlight_measurement(self, index: int | None) -> None:
        """Highlight one measurement annotation (screen only, never in PDF)."""
        self._highlight_index = index
        self.invalidate()

    def zoom_to_measurement(self, bounds) -> None:
        """Fit a measurement's annotation bounds with some drawing context."""
        if not bounds:
            return
        x0, y0, x1, y1 = bounds
        doc_b = self.doc.bounds()
        span = max(doc_b[2] - doc_b[0], doc_b[3] - doc_b[1]) if doc_b else 100.0
        pad = max((x1 - x0), (y1 - y0), span * 0.06)
        self.view.fit((x0 - pad, y0 - pad, x1 + pad, y1 + pad), margin=0.05)
        self.update()

    def clear_measurements(self) -> None:
        self.doc.measurements.clear()
        self._highlight_index = None
        self.refresh()

    def visible_bounds(self):
        return self.view.visible_bounds()

    # -------------------------------------------------------------- rendering
    def _display_list(self):
        key = (self._revision, self.dark, self.show_lineweight,
               self.include_measurements, self._highlight_index)
        if self._dl is None or key != self._dl_key:
            self._dl = build(self.doc, dark=self.dark,
                             include_measurements=self.include_measurements,
                             use_lineweight=self.show_lineweight,
                             highlight_index=self._highlight_index)
            self._dl_key = key
        return self._dl

    def paintEvent(self, event):
        p = QPainter(self)
        bg = QColor(30, 32, 37) if self.dark else QColor(255, 255, 255)
        p.fillRect(self.rect(), bg)
        if self.grid:
            self._draw_grid(p)
        paint_display_list(p, self._display_list(), self.view, self.show_lineweight)
        self._draw_crosshair(p)
        self._draw_temp(p)
        self._draw_snap(p)
        self._draw_rubber(p)
        p.end()

    def _draw_grid(self, p: QPainter):
        if self.view.scale <= 0:
            return
        target_px = 64.0
        step = 10 ** round(math.log10(max(target_px / self.view.scale, 1e-9)))
        b = self.view.visible_bounds()
        line = QColor(75, 78, 85, 60) if self.dark else QColor(205, 212, 222, 150)
        axis = QColor(110, 120, 140, 110) if self.dark else QColor(150, 165, 190, 190)
        pen = QPen(line)
        pen.setWidthF(0.6)
        p.setPen(pen)
        x = math.floor(b[0] / step) * step
        while x <= b[2] and (x - b[0]) * self.view.scale < 20000:
            sx = self.view.to_screen((x, 0))[0]
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            x += step
        y = math.floor(b[1] / step) * step
        while y <= b[3] and (y - b[1]) * self.view.scale < 20000:
            sy = self.view.to_screen((0, y))[1]
            p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            y += step
        p.setPen(QPen(axis, 1.0))
        sx0 = self.view.to_screen((0, 0))[0]
        sy0 = self.view.to_screen((0, 0))[1]
        p.drawLine(QPointF(sx0, 0), QPointF(sx0, self.height()))
        p.drawLine(QPointF(0, sy0), QPointF(self.width(), sy0))

    def _draw_crosshair(self, p: QPainter):
        if not self.cursor_screen:
            return
        pen = QPen(QColor(120, 140, 170, 110))
        pen.setWidthF(0.7)
        p.setPen(pen)
        x, y = self.cursor_screen.x(), self.cursor_screen.y()
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))

    def _draw_temp(self, p: QPainter):
        if not self.points:
            return
        color = QColor(250, 155, 40)
        pen = QPen(color)
        pen.setWidthF(1.6)
        p.setPen(pen)
        pts = [QPointF(*self.view.to_screen(q)) for q in self.points]
        if self.cursor_model:
            pts.append(QPointF(*self.view.to_screen(self.cursor_model)))
        if len(pts) > 1:
            p.drawPolyline(pts)
        for q in pts[:-1]:
            p.drawEllipse(q, 3.0, 3.0)

    def _draw_snap(self, p: QPainter):
        """Distinct marker per snap kind, plus a readable label badge."""
        r = self.snap_result
        if not r:
            return
        q = QPointF(*self.view.to_screen(r.point))
        x, y, s = q.x(), q.y(), 7.0
        marker = QColor(70, 225, 125) if self.dark else QColor(18, 145, 70)
        pen = QPen(marker)
        pen.setWidthF(1.8)
        p.setPen(pen)
        shape = snapping.MARKER_SHAPES.get(r.kind, "square")
        if shape == "square":
            p.drawRect(QRectF(x - s, y - s, 2 * s, 2 * s))
        elif shape == "triangle":
            p.drawPolygon(QPolygonF([QPointF(x, y - s),
                                     QPointF(x - s, y + s * 0.8),
                                     QPointF(x + s, y + s * 0.8)]))
        elif shape == "circle":
            p.drawEllipse(q, s, s)
            p.drawLine(QPointF(x - s - 3, y), QPointF(x + s + 3, y))
            p.drawLine(QPointF(x, y - s - 3), QPointF(x, y + s + 3))
        elif shape == "diamond":
            p.drawPolygon(QPolygonF([QPointF(x, y - s - 1), QPointF(x + s + 1, y),
                                     QPointF(x, y + s + 1), QPointF(x - s - 1, y)]))
        elif shape == "cross":
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x - s, y + s), QPointF(x + s, y - s))
        elif shape == "angle":
            p.drawLine(QPointF(x - s, y - s), QPointF(x - s, y + s))
            p.drawLine(QPointF(x - s, y + s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y - s))
        elif shape == "hourglass":
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y + s))
            p.drawLine(QPointF(x + s, y - s), QPointF(x - s, y + s))
            p.drawLine(QPointF(x - s, y - s), QPointF(x + s, y - s))
        elif shape == "plus":
            p.drawLine(QPointF(x - s, y), QPointF(x + s, y))
            p.drawLine(QPointF(x, y - s), QPointF(x, y + s))
        else:  # dot
            p.drawEllipse(q, 3.0, 3.0)

        font = ui_font(9)
        p.setFont(font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(r.label) + 10
        badge = QColor(18, 24, 22, 205) if self.dark else QColor(240, 255, 244, 225)
        p.fillRect(QRectF(x + 10, y - 22, w, 17), badge)
        p.setPen(QColor(225, 255, 235) if self.dark else QColor(10, 90, 45))
        p.drawText(QPointF(x + 15, y - 9), r.label)

    def _draw_rubber(self, p: QPainter):
        if not self._rubber:
            return
        a, b = self._rubber
        pen = QPen(QColor(40, 110, 220))
        pen.setWidthF(1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(a.x(), a.y(), b.x() - a.x(), b.y() - a.y())

    # ------------------------------------------------------------ coordinates
    def _model_at(self, pos) -> Point:
        return self.view.to_model(pos.x(), pos.y())

    def _snapped(self, pos) -> Point:
        raw = self._model_at(pos)
        tol = self.snap_config.radius_px / max(self.view.scale, 1e-9)
        reference = self.points[-1] if self.points else None
        self.snap_result = snapping.find_snap(
            self.doc, raw, tol, self.snap_enabled, self.snap_config, reference)
        kind = self.snap_result.kind if self.snap_result else None
        if kind != self._last_snap_kind:
            self._last_snap_kind = kind
            self.snapHint.emit(self.snap_result.label if self.snap_result else "")
        q = self.snap_result.point if self.snap_result else raw
        if self.ortho and self.points:
            q = snapping.ortho_point(self.points[-1], q)
        return q

    # ------------------------------------------------------------- mouse input
    def mousePressEvent(self, e):
        pos = e.position()
        self.cursor_screen = pos
        if e.button() == Qt.MouseButton.MiddleButton or (
                e.button() == Qt.MouseButton.LeftButton and self.mode == "pan"):
            self._panning = True
            self._drag_last = QPoint(int(pos.x()), int(pos.y()))
            return
        if e.button() == Qt.MouseButton.RightButton:
            self._finish_or_cancel()
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self.mode == "zoom_window":
            self._rubber = (pos, pos)
            self._drag_origin = QPoint(int(pos.x()), int(pos.y()))
            return
        if self.mode == "select":
            self._drag_origin = QPoint(int(pos.x()), int(pos.y()))
            self._drag_last = QPoint(int(pos.x()), int(pos.y()))
            self._window_selecting = False
            return
        self._handle_click(self._snapped(pos))

    def mouseMoveEvent(self, e):
        pos = e.position()
        self.cursor_screen = pos
        self.cursor_model = self._snapped(pos)
        self.coordinateMessage.emit(
            f"X {self.cursor_model[0]:.3f}   Y {self.cursor_model[1]:.3f}   "
            f"比例 1:{1 / max(self.view.scale, 1e-9):.3f}")
        if self._panning and self._drag_last:
            dx, dy = pos.x() - self._drag_last.x(), pos.y() - self._drag_last.y()
            self.view.pan(dx, dy)
            self._drag_last = QPoint(int(pos.x()), int(pos.y()))
            self.update()
            return
        if self._rubber and self.mode == "zoom_window":
            self._rubber = (self._rubber[0], pos)
            self.update()
            return
        if self._drag_origin and self.mode == "select":
            if abs(pos.x() - self._drag_origin.x()) + abs(pos.y() - self._drag_origin.y()) > 5:
                self._window_selecting = True
                self._rubber = (QPointF(self._drag_origin), pos)
            self.update()
            return
        self.update()

    def mouseReleaseEvent(self, e):
        pos = e.position()
        if self._panning:
            self._panning = False
            self._drag_last = None
            return
        if self.mode == "zoom_window" and self._rubber:
            a, b = self._rubber
            p1 = self.view.to_model(min(a.x(), b.x()), max(a.y(), b.y()))
            p2 = self.view.to_model(max(a.x(), b.x()), min(a.y(), b.y()))
            if abs(p2[0] - p1[0]) > 1e-9 and abs(p2[1] - p1[1]) > 1e-9:
                self.view.fit((p1[0], p1[1], p2[0], p2[1]), margin=0.0)
            self._rubber = None
            self.set_mode("select")
            self.update()
            return
        if self.mode == "select" and self._window_selecting and self._rubber:
            a, b = self._rubber
            p1 = self.view.to_model(min(a.x(), b.x()), max(a.y(), b.y()))
            p2 = self.view.to_model(max(a.x(), b.x()), min(a.y(), b.y()))
            ids = snapping.entities_in_rect(
                self.doc, (p1[0], p1[1], p2[0], p2[1]), editable_only=True)
            self._window_selecting = False
            self._rubber = None
            self._drag_origin = None
            if not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.doc.clear_selection()
            for ent in self.doc.entities:
                if ent.eid in ids:
                    ent.selected = True
            self._after_selection()
            return
        if self.mode == "select" and self._drag_origin:
            tol = 10.0 / max(self.view.scale, 1e-9)
            ent = snapping.nearest_entity(self.doc, self._model_at(QPointF(pos)), tol,
                                          editable_only=True)
            if not e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.doc.clear_selection()
            if ent:
                ent.selected = True
            self._drag_origin = None
            self._after_selection()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.mode in ("chain", "area", "draw_polyline"):
            self._finish_points()
        else:
            super().mouseDoubleClickEvent(e)

    def wheelEvent(self, e):
        factor = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        self.view.zoom(factor, (e.position().x(), e.position().y()))
        self.update()

    def keyPressEvent(self, e):
        key = e.key()
        if key == Qt.Key.Key_Escape:
            self._finish_or_cancel(force_cancel=True)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._finish_points()
        elif key == Qt.Key.Key_F3:
            self.statusMessage.emit(f"对象捕捉：{'开' if self.toggle_snap() else '关'}")
        elif key == Qt.Key.Key_F8:
            self.statusMessage.emit(f"正交：{'开' if self.toggle_ortho() else '关'}")
        elif key == Qt.Key.Key_F7:
            self.statusMessage.emit(f"栅格：{'开' if self.toggle_grid() else '关'}")
        elif key == Qt.Key.Key_Delete:
            self.delete_selected()
        else:
            super().keyPressEvent(e)

    # ---------------------------------------------------------------- editing
    def _after_selection(self):
        n = len(self.doc.selected_ids())
        self.selectionChanged.emit(n)
        self.invalidate()

    def delete_selected(self) -> None:
        if self.read_only:
            self._block_edit("delete")
            return
        selected = [e for e in self.doc.entities if e.selected]
        ids = {e.eid for e in selected if self.doc.editable(e)}
        skipped = len(selected) - len(ids)
        if not ids:
            self.statusMessage.emit("没有可编辑的选中对象（可能位于锁定图层）")
            return
        changed = self.undo_stack.perform(
            f"删除 {len(ids)} 个对象",
            lambda: setattr(self.doc, "entities",
                            [e for e in self.doc.entities if e.eid not in ids]))
        if changed:
            self.refresh()
            self.selectionChanged.emit(0)
            self.statusMessage.emit(
                f"已删除 {len(ids)} 个对象（可 Ctrl+Z 撤销）"
                + (f"；跳过 {skipped} 个锁定图层对象" if skipped else ""))

    def undo(self) -> None:
        if self.read_only:
            self._block_edit("undo")
            return
        label = self.undo_stack.undo()
        if label:
            self.refresh()
            self.statusMessage.emit(f"撤销：{label}")
        else:
            self.statusMessage.emit("没有可撤销的操作")

    def redo(self) -> None:
        if self.read_only:
            self._block_edit("redo")
            return
        label = self.undo_stack.redo()
        if label:
            self.refresh()
            self.statusMessage.emit(f"重做：{label}")
        else:
            self.statusMessage.emit("没有可重做的操作")

    def _add(self, ent: Entity) -> None:
        if self.read_only:
            self._block_edit("draw")
            return
        self.undo_stack.perform(f"绘制 {ent.kind}", lambda: self.doc.add(ent))
        self.refresh()
        self.statusMessage.emit(f"已添加 {ent.kind}")

    def _new(self, kind: str, data: dict) -> Entity:
        return Entity(0, kind, layer=self.current_layer, data=data)

    # ------------------------------------------------------------- tool logic
    def _handle_click(self, q: Point) -> None:
        m = self.mode
        if self.read_only and m in EDIT_MODES:
            # Defence in depth: a caller may have set .mode directly.
            self._block_edit(m)
            self._reset_to_select()
            return
        if m == "distance":
            self.points.append(q)
            if len(self.points) == 2:
                self._emit_measure(measure.distance(self.doc, *self.points))
                self.points.clear()
        elif m in ("chain", "area", "draw_polyline"):
            self.points.append(q)
        elif m == "radius":
            ent = snapping.nearest_entity(
                self.doc, q, 12.0 / max(self.view.scale, 1e-9))
            if ent and ent.kind in ("CIRCLE", "ARC"):
                result = measure.radius(self.doc, ent)
                if result:
                    self._emit_measure(result)
            else:
                self.statusMessage.emit("请点取圆或圆弧")
        elif m == "angle":
            self.points.append(q)
            if len(self.points) == 3:
                result = measure.angle_three_points(self.doc, *self.points)
                if result:
                    self._emit_measure(result)
                else:
                    self.statusMessage.emit("点与顶点重合，无法计算夹角")
                self.points.clear()
        elif m == "angle_lines":
            ent = snapping.nearest_entity(
                self.doc, q, 12.0 / max(self.view.scale, 1e-9))
            if not ent or not ent.segments():
                self.statusMessage.emit("请点取直线 / 多段线等线段对象")
            else:
                self._angle_entities.append(ent)
                if len(self._angle_entities) == 1:
                    self.statusMessage.emit(
                        f"已选第一条线（{ent.kind}），请点取第二条线")
                else:
                    result = measure.angle_between_lines(
                        self.doc, *self._angle_entities[:2])
                    self._angle_entities.clear()
                    if result:
                        self._emit_measure(result)
                    else:
                        self.statusMessage.emit("所选对象没有有效线段")
        elif m == "draw_line":
            self.points.append(q)
            if len(self.points) == 2:
                self._add(self._new("LINE", {"a": self.points[0], "b": self.points[1]}))
                self.points.clear()
        elif m == "draw_rect":
            self.points.append(q)
            if len(self.points) == 2:
                a, b = self.points
                pts = [a, (b[0], a[1]), b, (a[0], b[1])]
                self._add(self._new("LWPOLYLINE",
                                    {"pts": [(*p, 0.0) for p in pts], "closed": True}))
                self.points.clear()
        elif m == "draw_circle":
            self.points.append(q)
            if len(self.points) == 2:
                r = g.dist(*self.points)
                if r > 0:
                    self._add(self._new("CIRCLE", {"c": self.points[0], "r": r}))
                self.points.clear()
        elif m == "draw_arc":
            self.points.append(q)
            if len(self.points) == 3:
                c, s, e = self.points
                self._add(self._new("ARC", {
                    "c": c, "r": g.dist(c, s),
                    "start": g.angle_deg(c, s), "end": g.angle_deg(c, e)}))
                self.points.clear()
        elif m == "draw_text":
            text, ok = QInputDialog.getText(self, "文字", "输入文字内容：")
            if ok and text:
                h = 2.5 * max(1.0, (self.doc.bounds()[2] - self.doc.bounds()[0]) / 200.0) \
                    if self.doc.bounds() else 2.5
                self._add(self._new("TEXT",
                                    {"p": q, "text": text, "height": h,
                                     "rotation": 0.0, "width": 0.0}))
            self.set_mode("select")
        elif m in ("move", "copy", "rotate"):
            self.points.append(q)
            need = 3 if m == "rotate" else 2
            if len(self.points) == need:
                self._apply_transform(m)
                self.points.clear()
                self.set_mode("select")
        elif m == "mirror":
            self.points.append(q)
            if len(self.points) == 2:
                keep = QMessageBox.question(
                    self, "镜像", "是否保留原对象？\n"
                    "「是」= 复制后再镜像；「否」= 只保留镜像结果。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes
                self._apply_transform("mirror", keep)
                self.points.clear()
                self.set_mode("select")
        elif m == "scale":
            self.points.append(q)
            factor, ok = QInputDialog.getDouble(
                self, "缩放", "比例（> 0，例如 2 或 0.5）：", 2.0,
                0.0001, 10000.0, 4)
            if ok:
                self._apply_transform("scale", factor)
            self.points.clear()
            self.set_mode("select")
        else:
            self.points.clear()
        self.update()

    def _apply_transform(self, mode: str, param=None) -> None:
        if self.read_only:
            self._block_edit(mode)
            return
        selected = [e for e in self.doc.entities if e.selected]
        ids = {e.eid for e in selected if self.doc.editable(e)}
        skipped = len(selected) - len(ids)
        if not ids:
            self.statusMessage.emit("请先选中未锁定图层上的对象")
            return
        pts = self.points
        if mode in ("move", "copy"):
            dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
        elif mode == "rotate":
            ang = g.angle_deg(pts[0], pts[2]) - g.angle_deg(pts[0], pts[1])
        elif mode == "mirror":
            if g.dist(pts[0], pts[1]) < g.EPS:
                self.statusMessage.emit("镜像轴两点重合，已取消")
                return
        elif mode == "scale":
            if float(param) <= 0:
                self.statusMessage.emit("缩放比例必须大于 0")
                return
        label = {"move": "移动", "copy": "复制", "rotate": "旋转",
                 "mirror": "镜像", "scale": "缩放"}.get(mode, mode)

        def do():
            targets = [e for e in self.doc.entities if e.eid in ids]
            keep_source = mode == "copy" or (mode == "mirror" and param is True)
            if keep_source:
                clones = []
                for e in targets:
                    c = e.clone()
                    c.eid = 0
                    c.selected = True
                    clones.append(c)
                targets = clones
                for c in clones:
                    self.doc.add(c)
            for e in targets:
                if mode in ("move", "copy"):
                    e.transform("translate", dx, dy)
                elif mode == "rotate":
                    e.transform("rotate", pts[0], ang)
                elif mode == "mirror":
                    e.transform("mirror", pts[0], pts[1])
                elif mode == "scale":
                    e.transform("scale", pts[0], float(param))

        if self.undo_stack.perform(f"{label} {len(ids)} 个对象", do):
            self.refresh()
            self.statusMessage.emit(
                f"{label} 完成（{len(ids)} 个对象）"
                + (f"；跳过 {skipped} 个锁定图层对象" if skipped else ""))

    def _emit_measure(self, result: dict) -> None:
        if result:
            self.measurementAdded.emit(result)
            self.refresh()
            self.statusMessage.emit(f"测量结果：{result['label']}")

    def _finish_points(self) -> None:
        m = self.mode
        pts = list(self.points)
        self.points.clear()
        if m == "chain" and len(pts) >= 2:
            self._emit_measure(measure.chain(self.doc, pts))
        elif m == "area" and len(pts) >= 3:
            self._emit_measure(measure.area(self.doc, pts))
        elif m == "draw_polyline" and len(pts) >= 2:
            self._add(self._new("LWPOLYLINE",
                                {"pts": [(*p, 0.0) for p in pts], "closed": False}))
        self.update()

    def _finish_or_cancel(self, force_cancel: bool = False) -> None:
        if not force_cancel and self.mode in ("chain", "area", "draw_polyline") and self.points:
            self._finish_points()
            return
        self.points.clear()
        self._angle_entities.clear()
        self._rubber = None
        self._window_selecting = False
        if self.mode != "select":
            self.set_mode("select")
        else:
            self.doc.clear_selection()
            self._after_selection()
        self.update()
