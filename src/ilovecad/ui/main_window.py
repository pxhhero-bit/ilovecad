# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window controller. All layout lives in the .ui files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog,
                               QInputDialog, QLabel, QSizePolicy,
                               QListWidgetItem, QMainWindow, QMessageBox,
                               QTreeWidgetItem)

from .. import APP_NAME, APP_VERSION
from ..kernel.core import ImportReport, KernelError, LibreDwgKernel
from ..model.document import DwgDocument
from ..render.displaylist import build
from ..render.pdf import PdfOptions, PdfRenderer
from ..theme import apply_theme, is_dark, label as theme_label, load_theme, save_theme
from ..tools.measure import format_line
from ..version import TARGET_VERSION_LABEL
from .loader import load_ui

DWG_FILTER = "DWG / DXF 图纸 (*.dwg *.dxf);;所有文件 (*)"


def _localize_button_box(box: QDialogButtonBox) -> None:
    """Qt shows English stock labels on some locales; keep the UI all-Chinese."""
    labels = {
        QDialogButtonBox.StandardButton.Ok: "确定",
        QDialogButtonBox.StandardButton.Cancel: "取消",
        QDialogButtonBox.StandardButton.Close: "关闭",
        QDialogButtonBox.StandardButton.Apply: "应用",
        QDialogButtonBox.StandardButton.Reset: "重置",
    }
    for role, text in labels.items():
        button = box.button(role)
        if button is not None:
            button.setText(text)


class PdfExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "pdf_export")
        _localize_button_box(self.ui.buttonBox)
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)

    def options(self) -> PdfOptions:
        u = self.ui
        return PdfOptions(
            paper=u.comboPaper.currentText(),
            landscape=u.comboOrientation.currentIndex() == 0,
            scope=u.comboScope.currentText(),
            scale_denominator=int(u.spinScale.value()),
            monochrome=u.checkMonochrome.isChecked(),
            include_measurements=u.checkIncludeMeasure.isChecked(),
            use_lineweight=u.checkLineweight.isChecked(),
        )


class ImportReportDialog(QDialog):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "import_report")
        _localize_button_box(self.ui.buttonBox)
        self.ui.reportText.setPlainText(text)
        self.ui.buttonBox.rejected.connect(self.reject)


class ThemeSettingsDialog(QDialog):
    """Dark (default) / light application theme selector."""

    def __init__(self, current_theme: str, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "settings")
        _localize_button_box(self.ui.buttonBox)
        self.ui.radioDark.setChecked(is_dark(current_theme))
        self.ui.radioLight.setChecked(not is_dark(current_theme))
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)

    def theme(self) -> str:
        return "light" if self.ui.radioLight.isChecked() else "dark"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "about")
        _localize_button_box(self.ui.buttonBox)
        self.ui.aboutText.append(
            f"\n版本：{APP_VERSION}　内核写入版本：{TARGET_VERSION_LABEL}"
            "　许可：GPL-3.0-or-later")
        self.ui.buttonBox.rejected.connect(self.reject)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "main_window")
        self.kernel = LibreDwgKernel()
        self.doc = DwgDocument()
        self.current_path: Path | None = None
        self.report = ImportReport()
        self._loading_layers = False

        self.canvas = self.ui.canvas
        self.canvas.set_document(self.doc)
        # Dark is the default; a previous local choice is restored on startup.
        self.theme = apply_theme(load_theme())
        self.canvas.set_dark(is_dark(self.theme))
        self._build_status_bar()
        # Angle results carry several detail lines; let the narrow result panel
        # wrap them instead of clipping at the right edge.
        self.ui.measureList.setWordWrap(True)
        self.ui.measureList.setUniformItemSizes(False)
        self._connect_ui()
        self._populate_layers()
        self._update_title()
        self.ui.commandHint.setText(
            "就绪。打开图纸开始看图；滚轮缩放，中键或空格拖动平移，F3 捕捉，F8 正交。")
        self._status("纯净版 · 无广告 · 无会员 · 无订阅 · 2D Only")

    # ------------------------------------------------------------------ setup
    def _build_status_bar(self):
        self.lblCoord = QLabel("X 0.000   Y 0.000")
        self.lblScale = QLabel("比例 —")
        self.lblSnap = QLabel("捕捉 开")
        self.lblOrtho = QLabel("正交 关")
        self.lblGrid = QLabel("栅格 开")
        self.lblLW = QLabel("线宽 开")
        self.lblVersion = QLabel(TARGET_VERSION_LABEL)
        self.lbl2D = QLabel("2D ONLY")
        self.lblMessage = QLabel("")
        for w in (self.lblCoord, self.lblScale, self.lblSnap, self.lblOrtho,
                  self.lblGrid, self.lblLW, self.lblMessage):
            w.setStyleSheet("QLabel { padding: 0 9px; }")
        for w in (self.lblCoord, self.lblScale, self.lblSnap, self.lblOrtho,
                  self.lblGrid, self.lblLW):
            self.ui.statusbar.addWidget(w)
        self.lblMessage.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Preferred)
        self.ui.statusbar.addWidget(self.lblMessage, 1)
        self.ui.statusbar.addPermanentWidget(self.lbl2D)
        self.ui.statusbar.addPermanentWidget(self.lblVersion)

    def _status(self, msg: str) -> None:
        """Show a status message without overlapping the status labels."""
        self.lblMessage.setText(msg)

    def _connect_ui(self):
        u = self.ui
        c = self.canvas
        c.statusMessage.connect(self._status)
        c.coordinateMessage.connect(self.lblCoord.setText)
        c.hintMessage.connect(u.commandHint.setText)
        c.measurementAdded.connect(self._on_measurement)
        c.selectionChanged.connect(self._on_selection)
        c.documentEdited.connect(self._on_document_edited)

        tool_map = {
            "btnOpen": self.open_file_dialog, "btnSave": self.save,
            "btnSaveAs": self.save_as, "btnImportReport": self.show_report,
            "btnAbout": self.show_about,
            "btnFit": c.fit_view, "btnZoomIn": c.zoom_in,
            "btnZoomOut": c.zoom_out,
            "btnZoomWindow": lambda: c.set_mode("zoom_window"),
            "btnPan": lambda: c.set_mode("pan"),
            "btnDark": self._toggle_dark,
            "btnThemeSettings": self._open_theme_settings,
            "btnDrawLine": lambda: c.set_mode("draw_line"),
            "btnDrawPolyline": lambda: c.set_mode("draw_polyline"),
            "btnDrawRect": lambda: c.set_mode("draw_rect"),
            "btnDrawCircle": lambda: c.set_mode("draw_circle"),
            "btnDrawArc": lambda: c.set_mode("draw_arc"),
            "btnDrawText": lambda: c.set_mode("draw_text"),
            "btnSelect": lambda: c.set_mode("select"),
            "btnUndo": c.undo, "btnRedo": c.redo,
            "btnMove": lambda: self._need_selection("move"),
            "btnCopy": lambda: self._need_selection("copy"),
            "btnRotate": lambda: self._need_selection("rotate"),
            "btnDelete": lambda: c.set_mode("delete"),
            "btnMeasureDistance": lambda: c.set_mode("distance"),
            "btnMeasureChain": lambda: c.set_mode("chain"),
            "btnMeasureArea": lambda: c.set_mode("area"),
            "btnMeasureRadius": lambda: c.set_mode("radius"),
            "btnMeasureAngle": lambda: c.set_mode("angle"),
            "btnMeasureAngleLines": lambda: c.set_mode("angle_lines"),
            "btnMeasureClear": self._clear_measurements,
            "btnExportPdf": self.export_pdf, "btnExportDxf": self.export_dxf,
            "btnLayerAllOn": self._layers_all_on,
            "btnLayerInvert": self._layers_invert,
        }
        for name, slot in tool_map.items():
            widget = getattr(u, name, None)
            if widget is not None:
                widget.clicked.connect(slot)

        action_map = {
            "actOpen": self.open_file_dialog, "actSave": self.save,
            "actSaveAs": self.save_as, "actExportPdf": self.export_pdf,
            "actExportDxf": self.export_dxf, "actQuit": self.close,
            "actFit": c.fit_view, "actZoomIn": c.zoom_in,
            "actZoomOut": c.zoom_out, "actUndo": c.undo, "actRedo": c.redo,
            "actDelete": lambda: c.set_mode("delete"),
            "actMeasureDistance": lambda: c.set_mode("distance"),
            "actMeasureArea": lambda: c.set_mode("area"),
            "actMeasureAngle": lambda: c.set_mode("angle"),
            "actMeasureAngleLines": lambda: c.set_mode("angle_lines"),
            "actThemeSettings": self._open_theme_settings,
            "actAbout": self.show_about,
        }
        for name, slot in action_map.items():
            action = getattr(u, name, None)
            if action is not None:
                action.triggered.connect(slot)

        u.layerTree.itemChanged.connect(self._on_layer_changed)
        u.layerSearch.textChanged.connect(self._filter_layers)
        u.propTree.itemDoubleClicked.connect(self._on_property_double_clicked)
        u.commandInput.returnPressed.connect(self._on_command)

    # ------------------------------------------------------------------ files
    def _update_title(self):
        name = self.current_path.name if self.current_path else "未命名图纸"
        self.setWindowTitle(
            f"{name} — {APP_NAME} {APP_VERSION} · {TARGET_VERSION_LABEL} · 2D ONLY")

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开图纸", "", DWG_FILTER)
        if path:
            self.open_file(Path(path))

    def open_file(self, path):
        path = Path(path)
        try:
            doc, self.report = self.kernel.read(path)
        except KernelError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        self.doc = doc
        self.current_path = path
        self.canvas.set_document(doc)
        self._populate_layers()
        self.ui.reportBrowser.setPlainText(self.report.to_text())
        self.ui.measureList.clear()
        self._update_title()
        self._status(f"已打开 {path.name}｜{len(doc.entities)} 个 2D 实体｜"
            f"源版本 {doc.source_version or 'DXF'}"
            f"{'｜已源头降版本' if doc.downgraded else ''}")
        self.show_report_if_needed()

    def show_report_if_needed(self):
        # 报告已写入右侧「导入报告」面板；不自动弹模态窗口打断看图。
        if self.report.skipped or self.report.downgraded or self.report.warnings:
            self._status("导入存在跳过对象 / 源头降版本 / 警告，请点击「导入报告」查看明细")

    def show_report(self):
        ImportReportDialog(self.report.to_text(), self).exec()

    def show_about(self):
        AboutDialog(self).exec()

    def _confirm_data_loss(self) -> bool:
        n = sum(self.doc.skipped.values()) if self.doc.skipped else 0
        if not n and not self.doc.notes:
            return True
        details = "\n".join(f"  · {k} × {v}" for k, v in
                            list(self.doc.skipped.items())[:12])
        return QMessageBox.question(
            self, "保存前确认",
            f"本次经 DXF 中转，有 {n} 个不支持 / 3D 对象不会写入 R2004 文件"
            "（3D 支持按设计跳过）：\n" + details +
            "\n\n原文件中这些对象不会被静默改写；保存的是当前 2D 文档模型。"
            "\n确认继续保存吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def save(self):
        if self.current_path is None:
            self.save_as()
            return
        if not self._confirm_data_loss():
            return
        try:
            report = self.kernel.write(self.doc, self.current_path)
        except KernelError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.ui.reportBrowser.setPlainText(report.to_text())
        self._status(f"已保存 R2004 / AC1018：{self.current_path.name}（回读校验通过）")

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 R2004 图纸", "", "DWG 图纸 (*.dwg)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".dwg":
            target = target.with_suffix(".dwg")
        if not self._confirm_data_loss():
            return
        try:
            report = self.kernel.write(self.doc, target)
        except KernelError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.current_path = target
        self._update_title()
        self.ui.reportBrowser.setPlainText(report.to_text())
        self._status(f"已另存为 R2004 / AC1018：{target.name}（回读校验通过）")

    def export_pdf(self):
        dlg = PdfExportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出矢量 PDF", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".pdf":
            target = target.with_suffix(".pdf")
        opt = dlg.options()
        dl = build(self.doc, dark=False, monochrome=opt.monochrome,
                   include_measurements=opt.include_measurements,
                   use_lineweight=opt.use_lineweight)
        try:
            info = PdfRenderer.export(
                str(target), self.doc, dl, opt,
                self.canvas.visible_bounds() if opt.scope == "当前视图" else None)
        except Exception as exc:
            QMessageBox.critical(self, "PDF 导出失败", str(exc))
            return
        msg = (f"已导出矢量 PDF：{target.name}（独立渲染路径，未经 DWG 写入）"
               f"｜有效比例约 1:{info['effective_denominator']}")
        if info.get("clamped_to_fit"):
            msg += "｜已按纸张自动缩放以适应页面"
        self._status(msg)
        QMessageBox.information(self, "导出完成", msg)

    def export_dxf(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 DXF", "", "DXF 文件 (*.dxf)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".dxf":
            target = target.with_suffix(".dxf")
        self.kernel.export_dxf(self.doc, target)
        self._status(f"已导出 AC1018 DXF：{target.name}")

    # ----------------------------------------------------------------- layers
    def _populate_layers(self):
        self._loading_layers = True
        tree = self.ui.layerTree
        tree.clear()
        for name, layer in sorted(self.doc.layers.items(), key=lambda kv: kv[0]):
            item = QTreeWidgetItem(["", name, f"ACI {layer.color}"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if layer.visible
                               and not layer.frozen else Qt.CheckState.Unchecked)
            item.setForeground(1, QColor(*self._layer_color(layer.color)))
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(1)
        self._loading_layers = False

    @staticmethod
    def _layer_color(index: int):
        from ..render.colors import aci_rgb
        return aci_rgb(index)

    def _on_layer_changed(self, item, column):
        if self._loading_layers or column != 0:
            return
        name = item.text(1)
        layer = self.doc.layers.get(name)
        if layer:
            on = item.checkState(0) == Qt.CheckState.Checked
            layer.visible = on
            layer.frozen = not on
            self.canvas.invalidate()
            self._status(f"图层 {name}：{'显示' if on else '隐藏'}")

    def _filter_layers(self, text: str):
        t = text.strip().lower()
        for i in range(self.ui.layerTree.topLevelItemCount()):
            item = self.ui.layerTree.topLevelItem(i)
            item.setHidden(bool(t) and t not in item.text(1).lower())

    def _layers_all_on(self):
        for layer in self.doc.layers.values():
            layer.visible, layer.frozen = True, False
        self._populate_layers()
        self.canvas.invalidate()

    def _layers_invert(self):
        for layer in self.doc.layers.values():
            layer.visible = not layer.visible
            layer.frozen = not layer.visible
        self._populate_layers()
        self.canvas.invalidate()

    # -------------------------------------------------------------- properties
    def _on_selection(self, count: int):
        tree = self.ui.propTree
        tree.clear()
        if count == 0:
            tree.addTopLevelItem(QTreeWidgetItem(["提示", "未选中对象"]))
            return
        selected = [e for e in self.doc.entities if e.selected]
        first = selected[0]
        rows = [("选中数量", str(count)), ("类型", first.kind),
                ("图层", first.layer), ("颜色",
                                      "ByLayer" if first.color in (0, 256) else str(first.color)),
                ("线型", first.linetype), ("线宽", f"{first.lineweight:.2f} mm")]
        b = first.bounds()
        if b:
            rows += [("范围 X", f"{b[0]:.3f} ~ {b[2]:.3f}"),
                     ("范围 Y", f"{b[1]:.3f} ~ {b[3]:.3f}")]
        for k, v in rows:
            tree.addTopLevelItem(QTreeWidgetItem([k, v]))
        self._status(f"选中 {count} 个对象")

    def _on_property_double_clicked(self, item, column):
        if column != 1 or not self.doc.selected_ids():
            return
        key = item.text(0)
        ids = self.doc.selected_ids()
        if key == "图层":
            names = sorted(self.doc.layers)
            name, ok = QInputDialog.getItem(self, "修改图层", "选择图层：", names)
            if ok and name:
                def do():
                    for e in self.doc.entities:
                        if e.eid in ids:
                            e.layer = name
                self.canvas.undo_stack.perform(f"修改图层为 {name}", do)
                self.canvas.refresh()
                self._on_selection(len(ids))
        elif key == "颜色":
            value, ok = QInputDialog.getInt(self, "修改颜色", "ACI 颜色号 (1-255, 256=ByLayer)：",
                                            256, 1, 256)
            if ok:
                def do():
                    for e in self.doc.entities:
                        if e.eid in ids:
                            e.color = value
                self.canvas.undo_stack.perform("修改颜色", do)
                self.canvas.refresh()
                self._on_selection(len(ids))

    # ------------------------------------------------------------ measurements
    def _on_measurement(self, m: dict):
        item = QListWidgetItem(format_line(m))
        item.setData(Qt.ItemDataRole.UserRole, m)
        self.ui.measureList.addItem(item)
        self.ui.measureList.scrollToBottom()

    def _clear_measurements(self):
        self.canvas.clear_measurements()
        self.ui.measureList.clear()

    # ---------------------------------------------------------------- commands
    def _on_command(self):
        text = self.ui.commandInput.text().strip()
        self.ui.commandInput.clear()
        if not text:
            return
        cmd = text.upper()
        table = {
            "DI": "distance", "DIST": "distance", "L": "draw_line",
            "LINE": "draw_line", "PL": "draw_polyline", "REC": "draw_rect",
            "C": "draw_circle", "CIRCLE": "draw_circle", "A": "draw_arc",
            "T": "draw_text", "TEXT": "draw_text", "E": "delete",
            "M": "move", "CO": "copy", "RO": "rotate",
            "AREA": "area", "CHAIN": "chain", "R": "radius",
            "ANG": "angle", "ANGLE": "angle", "DAN": "angle",
            "AL": "angle_lines", "ANGLELINE": "angle_lines",
            "Z": None, "P": "pan", "SEL": "select", "ESC": "select",
        }
        if cmd in ("Z", "FIT"):
            self.canvas.fit_view()
            self._status("已适应全图")
            return
        if cmd == "PDF":
            self.export_pdf()
            return
        if cmd == "THEME":
            self._open_theme_settings()
            return
        if cmd == "DXF":
            self.export_dxf()
            return
        if cmd == "SAVE":
            self.save()
            return
        if cmd == "UNDO":
            self.canvas.undo()
            return
        if cmd == "REDO":
            self.canvas.redo()
            return
        if cmd == "SNAP":
            self.lblSnap.setText(f"捕捉 {'开' if self.canvas.toggle_snap() else '关'}")
            return
        if cmd == "ORTHO":
            self.lblOrtho.setText(f"正交 {'开' if self.canvas.toggle_ortho() else '关'}")
            return
        if cmd == "GRID":
            self.lblGrid.setText(f"栅格 {'开' if self.canvas.toggle_grid() else '关'}")
            return
        if cmd in table:
            mode = table[cmd]
            if mode in ("move", "copy", "rotate"):
                self._need_selection(mode)
            else:
                self.canvas.set_mode(mode)
        else:
            self._status(f"未知命令：{text}")

    def _need_selection(self, mode: str):
        if not self.doc.selected_ids():
            QMessageBox.information(self, "需要选择",
                                    "请先在图纸上选中对象，再执行该命令。")
            return
        self.canvas.set_mode(mode)

    # ------------------------------------------------------------------ theme
    def set_theme(self, theme: str, persist: bool = True) -> str:
        """Apply an application-wide theme and keep the canvas in sync."""
        self.theme = apply_theme(theme)
        self.canvas.set_dark(is_dark(self.theme))
        if persist:
            save_theme(self.theme)
        self._status(f"界面主题：{theme_label(self.theme)}"
                     + ("（已保存到本机，下次启动生效）" if persist else ""))
        return self.theme

    def _open_theme_settings(self):
        dlg = ThemeSettingsDialog(self.theme, self)
        if dlg.exec() == QDialog.Accepted:
            self.set_theme(dlg.theme())

    def _toggle_dark(self):
        dark = self.canvas.toggle_dark()
        self._status(("已切换为黑底" if dark else "已切换为白底")
                     + "（仅画布；整体主题请在「主题设置」中修改）")

    def _on_document_edited(self):
        self.lblScale.setText(f"比例 {self.canvas.view.scale:.4g}")
