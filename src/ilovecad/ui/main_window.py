# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window controller. All layout lives in the .ui files."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QColorDialog, QDialog,
                               QDialogButtonBox, QFileDialog, QInputDialog,
                               QLabel, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QSizePolicy, QTreeWidgetItem)

from .. import APP_NAME, APP_VERSION
from ..kernel.core import ImportReport, KernelError, LibreDwgKernel
from ..kernel.dxf_codec import parse_dxf
from ..model import geometry as g
from ..model import layer_ops
from ..model.document import DwgDocument
from ..model.session import (DocumentSession, empty_session, sha256_file)
from ..preferences import (DEFAULT_PRECISION, DEFAULT_UNITS, UNITS_CHOICES,
                           load_draw_color, load_text_settings,
                           load_units_precision, save_draw_color,
                           save_snap_config, save_text_settings,
                           save_units_precision, sanitize_text_settings,
                           sanitize_units_precision)
from ..render.displaylist import build
from ..render.pdf import PdfOptions, PdfRenderer
from ..theme import apply_theme, is_dark, label as theme_label, load_theme, save_theme
from ..tools import properties
from ..tools.measure import format_line
from ..tools.snapping import SnapConfig
from ..version import TARGET_VERSION, TARGET_VERSION_LABEL
from .batch_convert import BatchConvertDialog
from .canvas import EDIT_MODES
from .layer_manager import LayerManagerDialog
from .loader import load_ui

DWG_FILTER = "DWG / DXF 图纸 (*.dwg *.dxf);;所有文件 (*)"

# Checkbox widget name per stable snap kind. Keep in sync with settings.ui.
SNAP_CHECKBOXES = {
    "endpoint": "checkSnapEndpoint",
    "midpoint": "checkSnapMidpoint",
    "center": "checkSnapCenter",
    "quadrant": "checkSnapQuadrant",
    "intersection": "checkSnapIntersection",
    "perpendicular": "checkSnapPerpendicular",
    "nearest": "checkSnapNearest",
    "insertion": "checkSnapInsertion",
    "node": "checkSnapNode",
}


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


class SettingsDialog(QDialog):
    """Local-only settings: theme, typed object snapping, units / precision."""

    def __init__(self, current_theme: str, snap_config: SnapConfig | None = None,
                 parent=None, *, units: int = DEFAULT_UNITS,
                 precision: int = DEFAULT_PRECISION,
                 text_mode: str = "exact", text_scale: float = 1.0,
                 title_align: bool = True):
        super().__init__(parent)
        self.ui = load_ui(self, "settings")
        _localize_button_box(self.ui.buttonBox)
        self.ui.radioDark.setChecked(is_dark(current_theme))
        self.ui.radioLight.setChecked(not is_dark(current_theme))
        self._load_snap(snap_config or SnapConfig())
        self.ui.btnSnapDefaults.clicked.connect(
            lambda: self._load_snap(SnapConfig()))
        self.ui.checkSnapEnabled.toggled.connect(self._sync_snap_controls)
        self._sync_snap_controls()
        for code, text in UNITS_CHOICES:
            self.ui.comboUnits.addItem(text, code)
        self._load_units(units, precision)
        self.ui.btnUnitsDefaults.clicked.connect(
            lambda: self._load_units(DEFAULT_UNITS, DEFAULT_PRECISION))
        self._load_text(text_mode, text_scale)
        self.ui.checkTitleAlign.setChecked(bool(title_align))
        self.ui.btnTextDefaults.clicked.connect(
            lambda: (self._load_text("exact", 1.0),
                     self.ui.checkTitleAlign.setChecked(True)))
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)

    def _load_text(self, mode: str, scale: float) -> None:
        mode, scale = sanitize_text_settings(mode, scale)
        self.ui.radioTextAuto.setChecked(mode == "auto")
        self.ui.radioTextExact.setChecked(mode == "exact")
        self.ui.spinTextScale.setValue(int(round(scale * 100)))

    def _load_units(self, units: int, precision: int) -> None:
        code, places = sanitize_units_precision(units, precision)
        index = self.ui.comboUnits.findData(code)
        self.ui.comboUnits.setCurrentIndex(max(0, index))
        self.ui.spinPrecision.setValue(places)

    def _load_snap(self, cfg: SnapConfig) -> None:
        self.ui.checkSnapEnabled.setChecked(cfg.enabled)
        self.ui.spinSnapRadius.setValue(int(cfg.radius_px))
        for kind, name in SNAP_CHECKBOXES.items():
            widget = getattr(self.ui, name, None)
            if widget is not None:
                widget.setChecked(cfg.modes.get(kind, False))

    def _sync_snap_controls(self, _checked=None) -> None:
        on = self.ui.checkSnapEnabled.isChecked()
        self.ui.groupSnapKinds.setEnabled(on)
        self.ui.spinSnapRadius.setEnabled(on)

    def show_snap_page(self) -> None:
        self.ui.tabSettings.setCurrentWidget(self.ui.tabSnapping)

    def show_units_page(self) -> None:
        self.ui.tabSettings.setCurrentWidget(self.ui.tabUnits)

    def show_text_page(self) -> None:
        self.ui.tabSettings.setCurrentWidget(self.ui.tabText)

    def theme(self) -> str:
        return "light" if self.ui.radioLight.isChecked() else "dark"

    def snap_config(self) -> SnapConfig:
        return SnapConfig(
            enabled=self.ui.checkSnapEnabled.isChecked(),
            radius_px=float(self.ui.spinSnapRadius.value()),
            modes={kind: getattr(self.ui, name).isChecked()
                   for kind, name in SNAP_CHECKBOXES.items()})

    def units_precision(self) -> tuple[int, int]:
        code = self.ui.comboUnits.currentData()
        return sanitize_units_precision(code, self.ui.spinPrecision.value())

    def text_settings(self) -> tuple[str, float]:
        mode = "exact" if self.ui.radioTextExact.isChecked() else "auto"
        return sanitize_text_settings(mode, self.ui.spinTextScale.value() / 100.0)

    def title_align_enabled(self) -> bool:
        return self.ui.checkTitleAlign.isChecked()


# Older callers (and the command alias) still use the original name.
ThemeSettingsDialog = SettingsDialog


class EditModeDialog(QDialog):
    """Explicit, one-way degradation from a read-only session to editing."""

    def __init__(self, session: DocumentSession, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "edit_mode")
        _localize_button_box(self.ui.buttonBox)
        ok = self.ui.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        cancel = self.ui.buttonBox.button(QDialogButtonBox.StandardButton.Cancel)
        if ok:
            ok.setText("确认降级并进入编辑")
        if cancel:
            cancel.setText("保持只读")
        risks = session.risk_lines()
        self.ui.riskText.setPlainText(
            "降级风险清单\n"
            "============\n"
            + "\n".join(f"{i:2d}. {line}" for i, line in enumerate(risks, 1))
            + "\n\n取消：继续保持只读，源文件 / 导入快照 / 2D 模型均不变。\n"
              "确认：启用写入管线，之后可用原有编辑与保存功能。")
        self.ui.buttonBox.accepted.connect(self.accept)
        self.ui.buttonBox.rejected.connect(self.reject)


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
        # A blank unsaved sheet has no source to protect, so it starts
        # editable; every imported drawing starts in a read-only session.
        self.session: DocumentSession = empty_session(self.doc)
        self.current_path: Path | None = None
        self.report = ImportReport()
        self._loading_layers = False
        self._persist_snapping = True   # selftests can disable local writes
        self._snap_kind = ""
        self.units, self.precision = load_units_precision()
        self.doc.units, self.doc.precision = self.units, self.precision
        self.text_mode, self.text_scale = load_text_settings()
        self.doc.text_height_mode = self.text_mode
        self.doc.text_scale = self.text_scale
        self.draw_color = load_draw_color()

        self.canvas = self.ui.canvas
        self.canvas.set_document(self.doc)
        self.canvas.set_current_color(self.draw_color)
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
        self._refresh_units_label()
        self._status("纯净版 · 无广告 · 无会员 · 无订阅 · 2D Only")

    def _refresh_units_label(self) -> None:
        self.lblUnits.setText(f"{g.UNIT_LABELS.get(self.units, '单位')} · "
                              f"{self.precision} 位")

    def set_units_precision(self, units: int, precision: int,
                            persist: bool = True) -> tuple[int, int]:
        u, p = sanitize_units_precision(units, precision)
        self.units, self.precision = u, p
        self.doc.units, self.doc.precision = u, p
        if persist:
            save_units_precision(u, p)
        self._refresh_units_label()
        self._status(f"单位：{g.UNIT_LABELS.get(u, '单位')}，显示精度 {p} 位"
                     "（已写入 $INSUNITS，几何坐标不变）")
        return u, p

    # ---------------------------------------------------------------- colours
    @staticmethod
    def _color_swatch(rgb) -> QIcon:
        """Small icon: exact colour chip, or a split chip for 'ByLayer'."""
        size = 20
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        qp = QPainter(pix)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qp.setBrush(QColor(*rgb) if rgb else QColor(145, 158, 178))
        qp.setPen(QColor(70, 78, 92))
        qp.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
        if not rgb:
            qp.setPen(QColor(255, 255, 255))
            qp.drawLine(4, size - 4, size - 4, 4)
        qp.end()
        return QIcon(pix)

    def _refresh_color_ui(self, *_ignored) -> None:
        rgb = self.canvas.current_color
        self.lblColor.setText("颜色 随层" if rgb is None else
                              f"颜色 RGB {rgb[0]},{rgb[1]},{rgb[2]}")
        icon = self._color_swatch(rgb)
        for name in ("btnDrawColor", "actDrawColor"):
            widget = getattr(self.ui, name, None)
            if widget is None:
                continue
            widget.setIcon(icon)
            if hasattr(widget, "setIconSize"):
                widget.setIconSize(QSize(20, 20))
                widget.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

    def _apply_draw_color(self, rgb, persist: bool = True) -> None:
        rgb = self.canvas.set_current_color(rgb)
        self.draw_color = save_draw_color(rgb) if persist else rgb
        self._refresh_color_ui()
        self._status("绘图颜色：" + ("随层（ByLayer）" if rgb is None
                                    else f"RGB {rgb}"))

    def _choose_draw_color(self) -> None:
        """Pick the colour for newly drawn entities, or return to ByLayer."""
        box = QMessageBox(self)
        box.setWindowTitle("绘图颜色")
        box.setIcon(QMessageBox.Icon.Question)
        cur = self.canvas.current_color
        box.setText("设置新绘制实体的线条颜色。\n当前："
                    + ("随层（ByLayer）" if cur is None else f"RGB {cur}")
                    + "\n已绘制的对象不会被改动；需要改现有线条请用「线条改色」。")
        pick = box.addButton("选择颜色…", QMessageBox.ButtonRole.ActionRole)
        layer = box.addButton("恢复随层", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is layer:
            self._apply_draw_color(None)
            return
        if clicked is not pick:
            return
        initial = QColor(*(cur or (230, 90, 40)))
        col = QColorDialog.getColor(
            initial, self, "选择绘图颜色",
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if col.isValid():
            self._apply_draw_color((col.red(), col.green(), col.blue()))

    def _change_selected_color(self) -> None:
        """Batch-recolor selected entities through an undoable command."""
        if not self.doc.selected_ids():
            QMessageBox.information(self, "需要选择",
                                    "请先在图纸上选中要改色的对象，再执行该命令。")
            return
        if not self._guard_edit():
            return
        cur = self.canvas.current_color or (230, 90, 40)
        col = QColorDialog.getColor(
            QColor(*cur), self, "修改选中对象的线条颜色",
            QColorDialog.ColorDialogOption.DontUseNativeDialog)
        if not col.isValid():
            return
        rgb = (col.red(), col.green(), col.blue())
        changed = self.canvas.change_selected_color(rgb)
        if changed:
            self._on_selection(changed)
            self._status(f"已把 {changed} 个对象改为 RGB {rgb}（Ctrl+Z 可撤销）")

    def _command_color_arg(self, arg: str) -> bool:
        """``COLOR 1`` (ACI) or ``COLOR 255,0,0`` (true colour)."""
        from ..render.colors import aci_rgb, normalize_rgb
        s = arg.strip()
        if s.upper() in ("BYLAYER", "BYBLOCK", "随层"):
            self._apply_draw_color(None)
            return True
        rgb = None
        if "," in s:
            try:
                rgb = normalize_rgb(tuple(
                    int(v.strip()) for v in s.replace("，", ",").split(",")))
            except ValueError:
                rgb = None
        if rgb is None:
            try:
                aci = int(s)
                if 1 <= aci <= 256:
                    rgb = aci_rgb(aci)
            except ValueError:
                rgb = None
        if rgb is None:
            self._status("颜色参数无效：用 ACI 号（1–256）、R,G,B 或 BYLAYER")
            return True
        self._apply_draw_color(rgb)
        return True

    # ------------------------------------------------------------------ setup
    def _build_status_bar(self):
        self.lblCoord = QLabel("X 0.000   Y 0.000")
        self.lblScale = QLabel("比例 —")
        self.lblSnap = QLabel("捕捉 开" if self.canvas.snap_enabled else "捕捉 关")
        self.lblOrtho = QLabel("正交 关")
        self.lblGrid = QLabel("栅格 开")
        self.lblLW = QLabel("线宽 开")
        self.lblColor = QLabel("颜色 随层")
        self.lblUnits = QLabel("")
        self.lblMode = QLabel("模式：可编辑（空白图纸）")
        self.lblVersion = QLabel(TARGET_VERSION_LABEL)
        self.lbl2D = QLabel("2D ONLY")
        self.lblMessage = QLabel("")
        for w in (self.lblCoord, self.lblScale, self.lblSnap, self.lblOrtho,
                  self.lblGrid, self.lblLW, self.lblColor, self.lblUnits,
                  self.lblMode, self.lblMessage):
            w.setStyleSheet("QLabel { padding: 0 9px; }")
        for w in (self.lblCoord, self.lblScale, self.lblSnap, self.lblOrtho,
                  self.lblGrid, self.lblLW, self.lblColor, self.lblUnits,
                  self.lblMode):
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
        c.snapStateChanged.connect(self._on_snap_state)
        c.snapHint.connect(self._on_snap_hint)
        c.editBlocked.connect(self._on_edit_blocked)
        c.contextMenuRequested.connect(self._show_canvas_context_menu)
        c.currentColorChanged.connect(self._refresh_color_ui)

        tool_map = {
            "btnOpen": self.open_file_dialog, "btnSave": self.save,
            "btnSaveAs": self.save_as, "btnImportReport": self.show_report,
            "btnAbout": self.show_about,
            "btnFit": lambda: c.fit_view(), "btnZoomIn": c.zoom_in,
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
            "btnDrawColor": lambda *_: self._choose_draw_color(),
            "btnChangeColor": lambda *_: self._change_selected_color(),
            "btnSelect": lambda: c.set_mode("select"),
            "btnUndo": self._undo_guarded, "btnRedo": self._redo_guarded,
            "btnMove": lambda: self._need_selection("move"),
            "btnCopy": lambda: self._need_selection("copy"),
            "btnRotate": lambda: self._need_selection("rotate"),
            "btnMirror": lambda: self._need_selection("mirror"),
            "btnScale": lambda: self._need_selection("scale"),
            "btnDelete": lambda: c.set_mode("delete"),
            "btnMeasureDistance": lambda: c.set_mode("distance"),
            "btnMeasureChain": lambda: c.set_mode("chain"),
            "btnMeasureArea": lambda: c.set_mode("area"),
            "btnMeasureRadius": lambda: c.set_mode("radius"),
            "btnMeasureAngle": lambda: c.set_mode("angle"),
            "btnMeasureAngleLines": lambda: c.set_mode("angle_lines"),
            "btnMeasureClear": self._clear_measurements,
            "btnExportPdf": self.export_pdf, "btnExportDxf": self.export_dxf,
            "btnBatchConvert": self.open_batch_convert,
            "btnLayerAllOn": self._layers_all_on,
            "btnLayerInvert": self._layers_invert,
            "btnLayerManager": self.open_layer_manager,
            "btnEnterEdit": lambda: self._confirm_enter_edit(),
            "btnSaveCopy": lambda: self.save_source_copy(),
        }

        def guarded(slot):
            """QToolButton/QAction pass a `checked` bool; never forward it."""
            def run(*_ignored):
                return self._guard_edit(slot)
            return run

        edit_widgets = frozenset({
            "btnDrawLine", "btnDrawPolyline", "btnDrawRect", "btnDrawCircle",
            "btnDrawArc", "btnDrawText", "btnMove", "btnCopy", "btnRotate",
            "btnMirror", "btnScale", "btnDelete",
        })
        for name, slot in list(tool_map.items()):
            if name in edit_widgets:
                tool_map[name] = guarded(slot)
        for name, slot in tool_map.items():
            widget = getattr(u, name, None)
            if widget is not None:
                widget.clicked.connect(slot)

        action_map = {
            "actOpen": self.open_file_dialog, "actSave": self.save,
            "actSaveAs": self.save_as, "actExportPdf": self.export_pdf,
            "actExportDxf": self.export_dxf,
            "actBatchConvert": self.open_batch_convert, "actQuit": self.close,
            "actFit": lambda: c.fit_view(), "actZoomIn": c.zoom_in,
            "actZoomOut": c.zoom_out, "actUndo": self._undo_guarded,
            "actRedo": self._redo_guarded,
            "actDelete": lambda: c.set_mode("delete"),
            "actMeasureDistance": lambda: c.set_mode("distance"),
            "actMeasureArea": lambda: c.set_mode("area"),
            "actMeasureAngle": lambda: c.set_mode("angle"),
            "actMeasureAngleLines": lambda: c.set_mode("angle_lines"),
            "actThemeSettings": self._open_theme_settings,
            "actSnapSettings": self._open_snap_settings,
            "actTextSettings": self._open_text_settings,
            "actUnitsSettings": self._open_units_settings,
            "actLayerManager": self.open_layer_manager,
            "actMirror": lambda: self._need_selection("mirror"),
            "actScale": lambda: self._need_selection("scale"),
            "actDrawColor": lambda *_: self._choose_draw_color(),
            "actChangeColor": lambda *_: self._change_selected_color(),
            "actCommandHelp": self._show_command_help,
            "actAbout": self.show_about,
            "actEnterEdit": lambda: self._confirm_enter_edit(),
            "actSaveSourceCopy": lambda: self.save_source_copy(),
        }
        edit_actions = frozenset({
            "actDelete", "actMirror", "actScale",
        })
        for name, slot in list(action_map.items()):
            if name in edit_actions:
                action_map[name] = guarded(slot)
        for name, slot in action_map.items():
            action = getattr(u, name, None)
            if action is not None:
                action.triggered.connect(slot)

        u.layerTree.itemChanged.connect(self._on_layer_changed)
        u.layerTree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        u.layerTree.customContextMenuRequested.connect(self._layer_context_menu)
        u.layerSearch.textChanged.connect(self._filter_layers)
        u.propTree.itemDoubleClicked.connect(self._on_property_double_clicked)
        u.commandInput.returnPressed.connect(self._on_command)
        u.measureList.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        u.measureList.customContextMenuRequested.connect(self._measure_context_menu)
        u.measureList.itemClicked.connect(self._highlight_measurement)
        u.measureList.itemDoubleClicked.connect(self._locate_measurement)
        self._refresh_color_ui()

    # ------------------------------------------------------------ context menu

    # ------------------------------------------------------------ context menu
    def _show_canvas_context_menu(self, pos, has_selection: bool,
                                  *, execute: bool = True) -> QMenu:
        """Right-click menu on the canvas: every editing command in one place.

        ``execute=False`` builds the menu without showing it (used by the
        headless self test).  Read-only sessions keep the same menu; edit
        entries route through the explicit degradation gate, so nothing
        bypasses source protection.
        """
        c = self.canvas
        menu = QMenu(self)
        menu.setTitle("图纸操作")

        def act(parent, label, fn, enabled: bool = True, tip: str = ""):
            a = parent.addAction(label)
            a.setEnabled(enabled)
            if tip:
                a.setToolTip(tip)
            if fn is not None:
                a.triggered.connect(lambda *_ignored, f=fn: f())
            return a

        def editing(fn, needs_selection: bool = False):
            def run():
                if needs_selection and not self.doc.selected_ids():
                    QMessageBox.information(
                        self, "需要选择",
                        "请先在图纸上选中对象，再执行该命令。")
                    return
                self._guard_edit(fn)
            return run

        if self.session.read_only:
            act(menu, "进入编辑模式（确认降级风险）…",
                lambda: self._confirm_enter_edit())
            menu.setToolTip("当前只读；编辑功能会先要求确认降级")
        else:
            act(menu, "恢复只读 / 放弃编辑…",
                lambda: self._restore_readonly())
        act(menu, "选择", lambda: c.set_mode("select"))

        def clear_selection():
            self.doc.clear_selection()
            c._after_selection()
            c.invalidate()

        act(menu, "取消选择", clear_selection)
        menu.addSeparator()

        modify = menu.addMenu("修改选中对象(&M)")
        tip = "请先在图纸上选中对象" if not has_selection else ""
        for label, mode in (("移动（实时预览）", "move"),
                            ("复制（实时预览）", "copy"),
                            ("旋转（实时预览）", "rotate"),
                            ("镜像（实时预览）", "mirror"),
                            ("缩放（比例）", "scale")):
            act(modify, label, editing(lambda m=mode: c.set_mode(m), True),
                has_selection, tip)
        act(modify, "删除", editing(lambda: c.set_mode("delete"), True),
            has_selection, tip)
        act(modify, "线条改色…", lambda: self._change_selected_color(),
            has_selection, tip)

        draw = menu.addMenu("绘制(&D)")
        for label, mode in (("直线", "draw_line"),
                            ("多段线", "draw_polyline"),
                            ("矩形", "draw_rect"),
                            ("圆", "draw_circle"),
                            ("圆弧", "draw_arc"),
                            ("文字", "draw_text")):
            act(draw, label, editing(lambda m=mode: c.set_mode(m)))
        act(draw, "绘图颜色…", lambda: self._choose_draw_color())

        history = menu.addMenu("编辑历史与图层(&H)")
        act(history, "撤销 (Ctrl+Z)", lambda: self._undo_guarded(),
            not self.session.read_only)
        act(history, "重做 (Ctrl+Y)", lambda: self._redo_guarded(),
            not self.session.read_only)
        act(history, "图层管理…", lambda: self.open_layer_manager())
        act(history, "属性面板（双击属性值可改）",
            lambda: self.ui.propTree.setFocus())

        view = menu.addMenu("视图(&V)")
        act(view, "适应全图 (Z)", lambda: c.fit_view())
        act(view, "完整范围（含离群对象）", lambda: c.fit_view(full=True))
        act(view, "窗口缩放", lambda: c.set_mode("zoom_window"))
        act(view, "平移", lambda: c.set_mode("pan"))
        act(view, "放大", lambda: c.zoom_in())
        act(view, "缩小", lambda: c.zoom_out())
        act(view, "栅格开关 (F7)", lambda: self.lblGrid.setText(
            f"栅格 {'开' if c.toggle_grid() else '关'}"))

        measure_menu = menu.addMenu("测量(&S)")
        for label, mode in (("测距离", "distance"), ("累计长度", "chain"),
                            ("测面积", "area"), ("半径 / 直径", "radius"),
                            ("夹角（三点）", "angle"),
                            ("夹角（两线）", "angle_lines")):
            act(measure_menu, label, lambda m=mode: c.set_mode(m))

        menu.addSeparator()
        act(menu, "导出矢量 PDF…", lambda: self.export_pdf())
        act(menu, "另存原样副本（无损）…", lambda: self.save_source_copy())
        act(menu, "命令与快捷键说明 (F1)",
            lambda: self._show_command_help())
        if execute:
            menu.exec(c.mapToGlobal(pos))
        return menu

    # ------------------------------------------------------------------ files
    def _update_title(self):
        name = (self.current_path.name if self.current_path
                else self.session.source_name)
        self.setWindowTitle(
            f"{name} [{self.session.mode_text()}] — {APP_NAME} {APP_VERSION} "
            f"· {TARGET_VERSION_LABEL} · 2D ONLY")

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开图纸", "", DWG_FILTER)
        if path:
            self.open_file(Path(path))

    def open_file(self, path):
        path = Path(path)
        if not self.session.read_only and self.canvas.undo_stack.can_undo:
            if QMessageBox.question(
                    self, "未保存的编辑",
                    "当前图纸处于编辑模式且有未撤销的编辑；打开新图纸会丢弃这些"
                    "改动（源文件不会被改写）。建议先「另存为」。继续打开吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        try:
            session = self.kernel.read_session(path)
        except KernelError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))
            return
        except Exception as exc:  # malformed DXF must never crash the viewer
            QMessageBox.critical(
                self, "打开失败",
                f"文件解析时发生异常（{type(exc).__name__}：{exc}）。"
                "原文件未被改写，请检查图纸是否损坏。")
            return
        self.session = session
        self.report = session.report
        doc = self.doc = session.document
        # doc.units drives measurement / PDF display; source_units keeps the
        # drawing's own $INSUNITS so saving never silently relabels it.
        doc.units, doc.precision = self.units, self.precision
        doc.text_height_mode, doc.text_scale = self.text_mode, self.text_scale
        self.current_path = path
        self.canvas.set_document(doc)
        self._apply_mode_state()          # read-only gate before any interaction
        self._populate_layers()
        self._refresh_units_label()
        self.ui.reportBrowser.setPlainText(
            self.report.to_text() + "\n" + session.summary_text())
        self.ui.measureList.clear()
        self._status(f"只读打开 {path.name}｜{len(doc.entities)} 个 2D 实体｜"
            f"源版本 {doc.source_version or 'DXF'}"
            f"{'｜已源头降版本' if doc.downgraded else ''}")
        self.show_report_if_needed()

    def show_report_if_needed(self):
        # 报告已写入右侧「导入报告」面板；不自动弹模态窗口打断看图。
        _, ignored = self.doc.robust_bounds()
        parts: list[str] = []
        if self.session.read_only:
            parts.append("只读会话：编辑输入 EDIT / 点「进入编辑」；"
                         "另存为按字节复制原文件")
        if self.report.skipped or self.report.downgraded or \
                self.report.warnings or ignored:
            parts.append("导入存在跳过对象 / 源头降版本 / 警告，"
                         "请点「导入报告」查看明细")
        if ignored:
            parts.append(f"全图视图已忽略 {ignored} 个离群对象，"
                         "FITALL 查看完整范围")
        if parts:
            self._status("｜".join(parts))

    def show_report(self):
        ImportReportDialog(self.report.to_text(), self).exec()

    def show_about(self):
        AboutDialog(self).exec()

    # ---------------------------------------------- read-only / edit session
    def _apply_mode_state(self) -> None:
        """Keep the hard canvas gate, status label and title in sync."""
        self.canvas.set_read_only(self.session.read_only)
        self.lblMode.setText(f"模式：{self.session.mode_text()}")
        self._update_title()

    def _confirm_enter_edit(self) -> bool:
        """The only way out of read-only mode: an explicit risk acceptance."""
        if not self.session.read_only:
            self._status("当前已是编辑模式（2D 可编辑模型）")
            return True
        dlg = EditModeDialog(self.session, self)
        if dlg.exec() != QDialog.Accepted:
            self._status("已保持只读：源文件、导入快照与 2D 显示模型均未改动")
            return False
        self._enter_edit_mode()
        return True

    def _enter_edit_mode(self) -> None:
        """Degrade the read-only session into the original 2D write pipeline."""
        if not self.session.read_only:
            return
        self.session.enter_edit()
        self._apply_mode_state()
        self.ui.reportBrowser.setPlainText(
            self.report.to_text() + "\n" + self.session.summary_text())
        self._status(
            "已进入编辑模式：2D 可编辑模型已启用；保存仍锁定 R2004 / AC1018，"
            "源文件永不改写；输入 READONLY 可放弃编辑并从导入快照恢复只读")

    def _restore_readonly(self) -> None:
        """Discard in-memory edits and rebuild read-only from the DXF snapshot.

        No source file is read or written: the pristine importer snapshot kept
        in the session is the only input.  This makes degradation reversible
        without weakening the read-only source protection.
        """
        if self.session.read_only:
            self._status("当前已是只读会话")
            return
        if self.canvas.undo_stack.can_undo:
            if QMessageBox.question(
                    self, "放弃编辑",
                    "当前有未保存的编辑；恢复只读会丢弃这些改动"
                    "（源文件从未被改写）。继续吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No) != QMessageBox.Yes:
                return
        old = self.doc
        view = self.canvas.view
        saved_view = (view.scale, view.cx, view.cy)
        try:
            doc, _ = parse_dxf(self.session.import_dxf)
        except Exception as exc:  # a corrupted snapshot must not crash the app
            QMessageBox.critical(
                self, "恢复只读失败",
                f"导入快照解析异常（{type(exc).__name__}：{exc}）。"
                "仍保持编辑模式，源文件未被改写。")
            return
        doc.source_path = str(self.session.source_path) if self.session.source_path else None
        doc.source_version = self.session.source_version
        doc.effective_version = self.report.effective_version
        doc.downgraded = self.report.downgraded
        doc.units, doc.precision = self.units, self.precision
        doc.text_height_mode, doc.text_scale = self.text_mode, self.text_scale
        if old.align_title_text != doc.align_title_text:
            from ..model.titleblock import apply_title_block_alignment
            doc.align_title_text = old.align_title_text
            apply_title_block_alignment(doc, doc.align_title_text)
        self.session.restore_readonly(doc)
        self.doc = doc
        self.canvas.set_document(doc)
        self.canvas.view.scale, self.canvas.view.cx, self.canvas.view.cy = saved_view
        self._apply_mode_state()
        self._populate_layers()
        self._refresh_units_label()
        self.ui.reportBrowser.setPlainText(
            self.report.to_text() + "\n" + self.session.summary_text())
        self.ui.measureList.clear()
        self.canvas.invalidate()
        self._status(
            "已放弃编辑并恢复只读：2D 模型从会话内的导入 DXF 快照重建，"
            f"{len(doc.entities):,} 个实体；源文件 SHA-256 未变、从未改写")

    def _guard_edit(self, action=None) -> bool:
        """Run a mutating action, prompting for degradation when read-only."""
        if not self.session.read_only:
            if action is not None:
                action()
            return True
        if not self._confirm_enter_edit():
            return False
        if action is not None:
            action()
        return True

    def _on_edit_blocked(self, mode: str) -> None:
        # Emitted from inside a mouse/paint event: defer the modal dialog.
        QTimer.singleShot(0, self._confirm_enter_edit)

    def _undo_guarded(self) -> None:
        if self.session.read_only:
            self._status("只读会话没有编辑历史；如需编辑请输入 EDIT")
            return
        self.canvas.undo()

    def _redo_guarded(self) -> None:
        if self.session.read_only:
            self._status("只读会话没有编辑历史；如需编辑请输入 EDIT")
            return
        self.canvas.redo()

    def _readonly_block(self, what: str) -> None:
        """Read-only write attempt: offer a lossless copy or degradation."""
        box = QMessageBox(self)
        box.setWindowTitle("只读会话")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"当前为只读会话，不能{what}。\n\n"
            "源文件字节与导入 DXF 快照从未被改写。只读期间可用「原样副本」"
            "按字节复制一份完全相同的文件（无损、不经 DXF 重建）。\n"
            "需要修改图纸请选择「进入编辑」并确认降级风险。")
        copy_btn = box.addButton("另存原样副本…",
                                 QMessageBox.ButtonRole.ActionRole)
        edit_btn = box.addButton("进入编辑…",
                                 QMessageBox.ButtonRole.ActionRole)
        box.addButton("保持只读", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is copy_btn:
            self.save_source_copy()
        elif clicked is edit_btn:
            self._confirm_enter_edit()

    def save_source_copy(self) -> None:
        """Byte-for-byte copy of the original, independent of the 2D model."""
        src = self.session.source_path
        if src is None:
            QMessageBox.information(self, "无源文件",
                                    "当前是空白图纸，没有可复制的源文件。")
            return
        suffix = src.suffix.lower()
        path, _ = QFileDialog.getSaveFileName(
            self, "另存原样副本（无损）", "",
            f"{suffix.upper()} 图纸 (*{suffix})")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != suffix:
            target = target.with_suffix(suffix)
        try:
            report = self.kernel.copy_source(self.session, target)
        except (KernelError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "复制失败", str(exc))
            return
        self.ui.reportBrowser.setPlainText(report.to_text())
        self._status(f"已另存原样副本：{target.name}（SHA-256 与源文件一致，"
                     "未经 DXF 重建）")

    def _verify_source_unchanged(self) -> bool:
        """Refuse to write if the source was modified behind our back."""
        src = self.session.source_path
        if src is None or not self.session.source_sha256:
            return True
        if not src.exists():
            QMessageBox.critical(self, "源文件已变化",
                                 "源文件已不存在，已拒绝写入；请重新打开图纸。")
            return False
        try:
            changed = sha256_file(src) != self.session.source_sha256
        except OSError as exc:
            QMessageBox.critical(self, "源文件校验失败", str(exc))
            return False
        if changed:
            QMessageBox.critical(
                self, "源文件已变化",
                "源文件在打开之后被外部程序修改。为避免覆盖新内容，已拒绝写入；"
                "请重新打开图纸刷新只读会话。")
            return False
        return True

    # ------------------------------------------------------------ save safety
    APPROXIMATED = ("SPLINE", "HATCH", "MTEXT", "DIMENSION", "INSERT")

    def _source_protect_reason(self) -> str:
        """Why overwriting the currently opened file is not allowed."""
        if self.current_path is None:
            return ""
        src = self.session.source_path
        if src is not None and self.current_path.resolve() == src.resolve():
            return ("源文件保护：即使已进入编辑模式，也不能覆盖打开时的原始图纸；"
                    "请用「另存为」输出新的 R2004 / AC1018 文件")
        if self.current_path.suffix.lower() != ".dwg":
            return ("当前打开的是 DXF 文件，直接保存会把 DWG 二进制写入 .dxf "
                    "路径并破坏原文件")
        if self.doc.downgraded:
            return (f"源文件为高版本 {self.doc.source_version or 'DWG'}，"
                    "读入时已在临时副本上降版本；覆盖原图会造成不可逆的版本与数据降级")
        version = (self.doc.source_version or "").upper()
        if version and version != TARGET_VERSION:
            return (f"源文件版本 {version} 不是 {TARGET_VERSION}，"
                    "覆盖原图会改变其版本与内容")
        return ""

    def _save_risk_lines(self) -> list[str]:
        doc = self.doc
        lines: list[str] = []
        if doc.skipped:
            lines.append("导入时明确跳过的对象（不会写入）：" + "、".join(
                f"{k} × {v}" for k, v in sorted(doc.skipped.items())[:12]))
        counts = Counter(e.kind for e in doc.entities)
        approx = [(k, counts[k]) for k in self.APPROXIMATED if counts.get(k)]
        if approx:
            lines.append("按 2D 近似类型写出、几何可能失真：" + "、".join(
                f"{k} × {v}" for k, v in approx))
            if counts.get("SPLINE"):
                lines.append("SPLINE 已按 NURBS 求值后以 2D 折线写出"
                             "（非原生样条，折线长度不能代替原曲线长度）")
        if doc.source_units == 0:
            lines.append("源图纸 $INSUNITS=0（无单位）：写入时保留该标记，"
                         "测量数值仅按本机显示单位解释")
        if doc.notes:
            lines.append("导入降级说明：" + "；".join(doc.notes[:3]))
        return lines

    def _confirm_data_loss(self, target: Path | None = None,
                           overwrite: bool = False) -> bool:
        risks = self._save_risk_lines()
        if not risks:
            return True
        where = (f"将覆盖当前文件：{target}"
                 if overwrite and target else
                 f"将写入：{target}" if target else "将写入目标文件")
        detail = "\n".join(f"  · {line}" for line in risks)
        return QMessageBox.question(
            self, "保存前确认",
            f"{where}\n\n本次写出为 R2004 / AC1018。经 DXF 中转会发生以下变化，"
            "原文件无法自动恢复（建议优先使用「另存为」保留原件）：\n"
            + detail + "\n\n确认继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def save(self):
        if self.session.read_only:
            self._readonly_block("保存或覆盖任何 DWG 文件")
            return
        if not self._verify_source_unchanged():
            return
        if self.current_path is None:
            self.save_as()
            return
        reason = self._source_protect_reason()
        if reason:
            QMessageBox.information(
                self, "原文件保护",
                reason + "。\n\n为保证原图不被改写，请使用「另存为」输出新的 "
                "R2004 / AC1018 文件。")
            self.save_as()
            return
        if not self._confirm_data_loss(self.current_path, overwrite=True):
            return
        try:
            report = self.kernel.write(self.doc, self.current_path)
        except (KernelError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.ui.reportBrowser.setPlainText(report.to_text())
        self._status(f"已保存 R2004 / AC1018：{self.current_path.name}（回读校验通过）")

    def save_as(self):
        if self.session.read_only:
            # A read-only "Save As" must never rebuild the drawing: the useful
            # operation here is a lossless byte copy of the source file.
            self.save_source_copy()
            return
        if (not self.canvas.undo_stack.can_undo and
                self.session.source_path is not None):
            # The editable mode has produced no edits at all.  Rebuilding a
            # 5976-entity drawing through DXF would silently discard HATCH /
            # SPLINE / MTEXT semantics for no benefit.  Fall back to the
            # byte-identical copy; WRITER2004 still forces a rebuild.
            self._status(
                "未检测到任何编辑，已改用按字节「原样副本」（无损）；"
                "需要强制重建 R2004 请输入 WRITER2004")
            self.save_source_copy()
            return
        self._save_as_rebuild()

    def _save_as_rebuild(self):
        """Explicit R2004 rebuild of the editable 2D model."""
        if not self._verify_source_unchanged():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为 R2004 图纸", "", "DWG 图纸 (*.dwg)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".dwg":
            target = target.with_suffix(".dwg")
        blocked = {p.resolve() for p in
                   (self.current_path, self.session.source_path) if p is not None}
        if target.resolve() in blocked:
            QMessageBox.warning(self, "目标无效",
                                "不能覆盖当前打开的源文件，请换一个文件名或目录。")
            return
        if not self._confirm_data_loss(target, overwrite=target.exists()):
            return
        try:
            report = self.kernel.write(self.doc, target)
        except (KernelError, OSError, ValueError) as exc:
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
        if self.session.read_only:
            self._readonly_block("导出经 2D 模型重建的 AC1018 DXF")
            return
        if not self._verify_source_unchanged():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 DXF", "", "DXF 文件 (*.dxf)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".dxf":
            target = target.with_suffix(".dxf")
        src = self.session.source_path
        if src is not None and target.resolve() == src.resolve():
            QMessageBox.warning(self, "目标无效",
                                "不能覆盖当前打开的源文件，请换一个文件名或目录。")
            return
        try:
            self.kernel.export_dxf(self.doc, target)
        except (KernelError, OSError, ValueError) as exc:
            QMessageBox.critical(self, "DXF 导出失败", str(exc))
            return
        self._status(f"已导出 AC1018 DXF：{target.name}")

    def open_batch_convert(self):
        dlg = BatchConvertDialog(self.kernel, self)
        dlg.exec()
        self._status("批量转换窗口已关闭；输出目录中的原图纸未被改写")

    # ----------------------------------------------------------------- layers
    def _populate_layers(self):
        self._loading_layers = True
        tree = self.ui.layerTree
        tree.clear()
        current = self.canvas.current_layer
        for name, layer in sorted(self.doc.layers.items(), key=lambda kv: kv[0]):
            tag = " [锁定]" if layer.locked else ""
            item = QTreeWidgetItem(["", name, f"ACI {layer.color}{tag}"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if layer.visible
                               and not layer.frozen else Qt.CheckState.Unchecked)
            item.setForeground(1, QColor(*self._layer_color(layer.color)))
            if name == current:
                font = QFont()
                font.setBold(True)
                item.setFont(1, font)
            tree.addTopLevelItem(item)
        tree.resizeColumnToContents(1)
        self._loading_layers = False

    @staticmethod
    def _layer_color(index: int):
        from ..render.colors import aci_rgb
        return aci_rgb(index)

    def _layer_view_op(self, label: str, fn) -> None:
        """Visibility / freeze are view state, allowed even in read-only.

        A viewer session must not accumulate undo history for view-only state,
        so the operation bypasses the undo stack while the write gate is on.
        """
        if self.session.read_only:
            fn()
        else:
            self.canvas.undo_stack.perform(label, fn)

    def _on_layer_changed(self, item, column):
        if self._loading_layers or column != 0:
            return
        name = item.text(1)
        on = item.checkState(0) == Qt.CheckState.Checked
        self._layer_view_op(
            f"图层 {name} 显示状态",
            lambda: layer_ops.set_flags(self.doc, name, visible=on,
                                        frozen=False if on else None))
        self.canvas.invalidate()
        self._status(f"图层 {name}：{'显示' if on else '隐藏'}"
                     + ("（只读视图状态，不写回文件）" if self.session.read_only else ""))

    def _filter_layers(self, text: str):
        t = text.strip().lower()
        for i in range(self.ui.layerTree.topLevelItemCount()):
            item = self.ui.layerTree.topLevelItem(i)
            item.setHidden(bool(t) and t not in item.text(1).lower())

    def _layers_all_on(self):
        self._layer_view_op("全部图层显示",
                            lambda: layer_ops.all_on(self.doc))
        self._populate_layers()
        self.canvas.invalidate()

    def _layers_invert(self):
        self._layer_view_op("反转图层显示",
                            lambda: layer_ops.invert(self.doc))
        self._populate_layers()
        self.canvas.invalidate()

    def _set_current_layer(self, name: str) -> None:
        if name in self.doc.layers:
            self.canvas.current_layer = name
            self._populate_layers()
            self._status(f"当前绘制图层：{name}")

    def _toggle_layer_lock(self, name: str) -> None:
        layer = self.doc.layers.get(name)
        if layer is None or not self._guard_edit():
            return
        new = not layer.locked
        self.canvas.undo_stack.perform(
            f"图层 {name} 锁定状态",
            lambda: layer_ops.set_flags(self.doc, name, locked=new))
        self._populate_layers()
        self._status(f"图层 {name}：{'已锁定（可见但不可编辑）' if new else '已解锁'}")

    def _layer_context_menu(self, pos):
        tree = self.ui.layerTree
        item = tree.itemAt(pos)
        menu = QMenu(self)
        menu.addAction("图层管理…", self.open_layer_manager)
        if item:
            name = item.text(1)
            menu.addSeparator()
            menu.addAction(f"设为当前图层：{name}",
                           lambda: self._set_current_layer(name))
            menu.addAction("锁定 / 解锁此图层",
                           lambda: self._toggle_layer_lock(name))
            menu.addAction("全部显示", self._layers_all_on)
        menu.exec(tree.viewport().mapToGlobal(pos))

    def open_layer_manager(self):
        dlg = LayerManagerDialog(self.doc, self.canvas.current_layer,
                                 self.canvas.undo_stack, self,
                                 read_only=self.session.read_only)
        dlg.exec()
        changed = dlg.changed
        self.canvas.current_layer = dlg.current_layer()
        if self.canvas.current_layer not in self.doc.layers:
            self.canvas.current_layer = "0"
        self._populate_layers()
        self.canvas.invalidate()
        self._status(f"图层管理：当前 {self.canvas.current_layer}，"
                     f"共 {len(self.doc.layers)} 个图层"
                     + (f"；{changed} 项改动可用 Ctrl+Z 撤销" if changed else ""))
        return changed

    # -------------------------------------------------------------- properties
    def _on_selection(self, count: int):
        tree = self.ui.propTree
        tree.clear()
        for row in properties.rows(self.doc, self.doc.entities):
            item = QTreeWidgetItem([row.label, row.value])
            if row.key and row.editable:
                item.setData(0, Qt.ItemDataRole.UserRole, row.key)
                item.setToolTip(1, "双击编辑；锁定图层上的对象不会被修改")
            tree.addTopLevelItem(item)
        if count:
            self._status(
                f"选中 {count} 个对象"
                + ("（只读；双击属性会先提示降级风险）" if self.session.read_only
                   else "｜双击属性值可编辑"))

    def _on_property_double_clicked(self, item, column):
        if column != 1 or not self.doc.selected_ids():
            return
        if not self._guard_edit():
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        if key == "attr:color":
            # A colour is a visual choice, not a text field: open the picker.
            self._change_selected_color()
            return
        selected = [e for e in self.doc.entities if e.selected]
        label = item.text(0)
        current = item.text(1)
        choices = properties.choices_for(self.doc, key)
        if choices:
            index = choices.index(current) if current in choices else 0
            value, ok = QInputDialog.getItem(
                self, "修改属性", f"{label}：", choices, index, False)
        else:
            default = "" if current in (properties.MULTIPLE, "ByLayer") else current
            value, ok = QInputDialog.getText(
                self, "修改属性", f"{label}：\n坐标请写成 X,Y（例如 12.5,40）",
                text=default)
        if not ok:
            return
        edited = 0
        locked = properties.locked_count(self.doc, selected)

        def do():
            nonlocal edited
            edited = properties.apply_value(self.doc, selected, key, value)

        try:
            self.canvas.undo_stack.perform(f"修改 {label}", do)
        except ValueError as exc:
            QMessageBox.warning(self, "属性修改失败", str(exc))
            return
        self.canvas.refresh()
        self._on_selection(len(self.doc.selected_ids()))
        self._status(f"已修改 {label}：{edited} 个对象"
                     + (f"；跳过 {locked} 个锁定图层对象" if locked else ""))

    # ------------------------------------------------------------ measurements
    def _on_measurement(self, m: dict):
        item = QListWidgetItem(format_line(m))
        item.setData(Qt.ItemDataRole.UserRole, m)
        self.ui.measureList.addItem(item)
        self.ui.measureList.scrollToBottom()

    def _clear_measurements(self):
        self.canvas.clear_measurements()
        self.ui.measureList.clear()

    @staticmethod
    def _measure_of(item):
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _measurement_index(self, m: dict | None) -> int:
        if m is None:
            return -1
        for i, record in enumerate(self.doc.measurements):
            if record is m:
                return i
        for i, record in enumerate(self.doc.measurements):
            if record == m:
                return i
        return -1

    def _measurement_bounds(self, m: dict):
        pts: list[tuple[float, float]] = []
        for seq in (m.get("paths") or [m.get("points", [])]):
            pts.extend(tuple(p[:2]) for p in seq)
        if m.get("label_at"):
            pts.append(tuple(m["label_at"][:2]))
        return g.bounds_of(pts)

    def _highlight_measurement(self, item):
        idx = self._measurement_index(self._measure_of(item))
        self.canvas.set_highlight_measurement(idx if idx >= 0 else None)
        if idx >= 0:
            self._status(f"已高亮第 {idx + 1} 条测量标注；双击可定位到该标注")

    def _locate_measurement(self, item):
        m = self._measure_of(item)
        idx = self._measurement_index(m)
        if not m or idx < 0:
            self._status("该测量记录已不存在")
            return
        b = self._measurement_bounds(m)
        self.canvas.set_highlight_measurement(idx)
        self.canvas.zoom_to_measurement(b)
        self._status(f"已定位第 {idx + 1} 条测量标注：{m.get('label', '')}")

    def _delete_measurement(self, item):
        m = self._measure_of(item)
        idx = self._measurement_index(m)
        if idx < 0:
            return
        self.doc.measurements.pop(idx)
        self.ui.measureList.takeItem(self.ui.measureList.row(item))
        self.canvas.set_highlight_measurement(None)
        self.canvas.refresh()
        self._status(f"已删除第 {idx + 1} 条测量记录")

    def _copy_measurement(self, item):
        m = self._measure_of(item)
        if m:
            QApplication.clipboard().setText(str(m.get("label", "")))
            self._status(f"已复制：{m.get('label', '')}")

    def _export_measurements_csv(self):
        if not self.doc.measurements:
            self._status("没有可导出的测量记录")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出测量记录", "measurements.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".csv":
            target = target.with_suffix(".csv")
        try:
            with target.open("w", newline="", encoding="utf-8-sig") as fp:
                writer = csv.writer(fp)
                writer.writerow(["序号", "类型", "结果", "数值", "补充说明", "标注点数"])
                for i, m in enumerate(self.doc.measurements, 1):
                    writer.writerow([
                        i, m.get("kind", ""), m.get("label", ""),
                        m.get("value", ""),
                        " ".join(str(m.get("extra", "")).split()),
                        len(m.get("points", []))])
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._status(f"已导出 {len(self.doc.measurements)} 条测量记录：{target.name}")

    def _measure_context_menu(self, pos):
        item = self.ui.measureList.itemAt(pos)
        menu = QMenu(self)
        if item:
            menu.addAction("定位到标注", lambda: self._locate_measurement(item))
            menu.addAction("复制结果数值", lambda: self._copy_measurement(item))
            menu.addAction("删除此条记录", lambda: self._delete_measurement(item))
            menu.addSeparator()
        menu.addAction("导出全部测量记录 (CSV)…", self._export_measurements_csv)
        menu.addAction("清除所有结果", self._clear_measurements)
        menu.exec(self.ui.measureList.viewport().mapToGlobal(pos))

    def _show_command_help(self):
        self.ui.reportBrowser.setPlainText(
            "IloveCAD 命令与快捷键（在底部命令栏输入即可）\n"
            "========================================\n"
            "会话：打开图纸默认【只读】；EDIT / ED / EDITMODE 或点「进入编辑」\n"
            "      确认降级风险后，才启用 2D 可编辑模型；\n"
            "      READONLY / ABANDON 放弃编辑、从导入快照恢复只读；\n"
            "      未做任何编辑时「另存为」按字节无损复制，WRITER2004 强制重建；\n"
            "      SAVECOPY / COPYFILE 按字节另存原样副本（无损，SHA-256 校验）；\n"
            "      只读下「另存为」等同原样副本，保存 / DXF 重建导出被拒绝\n"
            "文件：OPEN / SAVE / DXF / PDF　（Ctrl+O / Ctrl+S / Ctrl+P / Ctrl+Shift+S）\n"
            "      只读期间仍可导出矢量 PDF、测量、图层显隐与对象捕捉\n"
            "视图：Z 或 FIT 适应全图、FITALL / ZEXTENTS 完整范围（含离群对象）、\n"
            "      P 平移、F7 栅格、F8 正交、GRID / ORTHO\n"
            "      PDF 范围：全图（主体图幅）/ 当前视图 / 完整范围（含离群对象）\n"
            "捕捉：F3 或 SNAP 开关；OSNAP / SNAPSET 打开捕捉设置；\n"
            "       端点 / 顶点、中点、中心 / 圆心、象限点、交点、垂足、最近点可单独开关\n"
            "绘制：L / LINE、PL、REC、C / CIRCLE、A、T / TEXT\n"
            "颜色：COLOR / COL 打开绘图颜色（可「恢复随层」）；COLOR 1 用 ACI 号，\n"
            "      COLOR 255,0,0 用真彩色；BYLAYER 恢复随层；\n"
            "      CHCOLOR / CHANGECOLOR / CC 批量修改选中对象的线条颜色；\n"
            "      属性面板双击「颜色」行同样打开取色器；新绘制实体带精确 RGB，\n"
            "      写回 R2004 同时保存 DXF 420 真彩色与最接近的 ACI 62\n"
            "编辑：E / DELETE、M / MOVE、CO / COPY、RO / ROTATE、MI / MIRROR、SC / SCALE、\n"
            "      UNDO / Ctrl+Z、REDO / Ctrl+Y；属性面板双击可改图层 / 颜色 / 线型 / 线宽 /\n"
            "      坐标 / 半径 / 文字内容，支持多选批量修改\n"
            "      移动 / 复制 / 旋转 / 镜像：点取基点后实时显示变换预览，原图淡化；\n"
            "      点取目标点落地，Esc 或右键取消。右键弹出完整编辑功能菜单\n"
            "图层：LA / LAYER 打开图层管理（新建 / 重命名 / 删除 / 锁定 / 颜色线型线宽）；\n"
            "      锁定图层仍可见可捕捉，但不能被选中编辑\n"
            "测量：DI / DIST、CHAIN、AREA、R、ANG / DAN（三点夹角）、AL / ANGLELINE（两线夹角）\n"
            "设置：THEME / SETTINGS / OP 主题与对象捕捉；OSNAP 捕捉页；UNITS 单位与精度页\n"
            "      STYLE / FONT 文字与字体页（字高默认原样 1:1，可选自适应与显示倍率）\n"
            "      TITLEALIGN 开关标题栏文字按图纸格线居中（仅显示层）\n"
            "帮助：HELP / ? 显示本说明\n"
            "\n测量结果列表：单击高亮，双击定位，右键可删除单条 / 复制数值 / 导出 CSV。\n"
            "鼠标：滚轮缩放，中键或「平移」模式左键拖动平移，Esc 取消当前命令。\n")
        self._status("已显示命令与快捷键说明（右下「导入报告」区域）")

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
            "MI": "mirror", "MIRROR": "mirror", "SC": "scale", "SCALE": "scale",
            "AREA": "area", "CHAIN": "chain", "R": "radius",
            "ANG": "angle", "ANGLE": "angle", "DAN": "angle",
            "AL": "angle_lines", "ANGLELINE": "angle_lines",
            "Z": None, "P": "pan", "SEL": "select", "ESC": "select",
        }
        if cmd in ("COLOR", "COL", "DRAWCOLOR", "SETCOLOR"):
            self._choose_draw_color()
            return
        if cmd.startswith("COLOR ") or cmd.startswith("COL "):
            self._command_color_arg(text.split(None, 1)[1])
            return
        if cmd in ("BYLAYER", "COLORBYLAYER", "CB"):
            self._apply_draw_color(None)
            return
        if cmd in ("CHCOLOR", "CHANGECOLOR", "RECOLOR", "CC"):
            self._change_selected_color()
            return
        if cmd in ("Z", "FIT"):
            self.canvas.fit_view()
            _, ignored = self.doc.robust_bounds()
            self._status("已适应全图" + (f"（忽略 {ignored} 个离群对象，"
                                     "FITALL 可查看完整范围）" if ignored else ""))
            return
        if cmd in ("FITALL", "ZEXTENTS", "EXTENTS"):
            self.canvas.fit_view(full=True)
            self._status("已按完整范围显示（包含离群对象）")
            return
        if cmd == "PDF":
            self.export_pdf()
            return
        if cmd in ("THEME", "SETTINGS", "OP", "OPTIONS", "PREFERENCES"):
            self._open_settings()
            return
        if cmd in ("OSNAP", "SNAPSET", "SNAPSETTINGS"):
            self._open_settings(snap_page=True)
            return
        if cmd in ("UNITS", "DDUNITS", "UN"):
            self._open_units_settings()
            return
        if cmd in ("STYLE", "FONT", "TEXTSET", "FONTS"):
            self._open_settings(text_page=True)
            return
        if cmd in ("TITLEALIGN", "TBALIGN", "TITLEALIGNMENT"):
            self._set_title_align(not self.doc.align_title_text)
            return
        if cmd in ("READONLY", "RO", "ABANDON", "DISCARD", "RESTORE"):
            self._restore_readonly()
            return
        if cmd in ("WRITER2004", "REBUILDR2004", "FORCESAVE"):
            if self.session.read_only:
                self._confirm_enter_edit()
            if not self.session.read_only:
                self._save_as_rebuild()
            return
        if cmd in ("LA", "LAYER", "LAYERS", "LM"):
            self.open_layer_manager()
            return
        if cmd in ("HELP", "?", "？"):
            self._show_command_help()
            return
        if cmd == "DXF":
            self.export_dxf()
            return
        if cmd in ("BATCH", "CONVERT"):
            self.open_batch_convert()
            return
        if cmd in ("EDIT", "ED", "EDITMODE", "UNPROTECT"):
            self._confirm_enter_edit()
            return
        if cmd in ("SAVECOPY", "COPYFILE", "SAVEASCOPY"):
            self.save_source_copy()
            return
        if cmd == "SAVE":
            self.save()
            return
        if cmd in ("UNDO", "REDO"):
            if self.session.read_only:
                self._status("只读会话没有编辑历史；如需编辑请输入 EDIT")
            elif cmd == "UNDO":
                self.canvas.undo()
            else:
                self.canvas.redo()
            return
        if cmd == "SNAP":
            self.canvas.toggle_snap()   # state signal updates label + local prefs
            return
        if cmd == "ORTHO":
            self.lblOrtho.setText(f"正交 {'开' if self.canvas.toggle_ortho() else '关'}")
            return
        if cmd == "GRID":
            self.lblGrid.setText(f"栅格 {'开' if self.canvas.toggle_grid() else '关'}")
            return
        if cmd in table:
            mode = table[cmd]
            if mode in EDIT_MODES:
                self._guard_edit(lambda m=mode: (
                    self._need_selection(m) if m in
                    ("move", "copy", "rotate", "mirror", "scale")
                    else self.canvas.set_mode(m)))
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

    # --------------------------------------------------------------- snapping
    def _refresh_snap_label(self) -> None:
        on = self.canvas.snap_enabled
        self.lblSnap.setText("捕捉 关" if not on else
                            f"捕捉 {self._snap_kind}" if self._snap_kind else "捕捉 开")

    def _on_snap_state(self, on: bool) -> None:
        self._snap_kind = ""
        self._refresh_snap_label()
        if self._persist_snapping:
            save_snap_config(self.canvas.snap_config)
        self._status(f"对象捕捉：{'开' if on else '关'}（设置已保存在本机）")

    def _on_snap_hint(self, kind: str) -> None:
        self._snap_kind = kind
        self._refresh_snap_label()

    # --------------------------------------------------------------- settings
    def _set_title_align(self, enabled: bool) -> None:
        """Toggle viewer-only snapping of labels to their title-block cells."""
        from ..model.titleblock import apply_title_block_alignment
        self.doc.align_title_text = bool(enabled)
        moved = apply_title_block_alignment(self.doc,
                                            self.doc.align_title_text)
        self.canvas.invalidate()
        self._status(
            f"标题栏格线对齐已{'开启' if enabled else '关闭'}："
            f"{moved} 个文字按图纸格线做显示层居中；源文件与写出数据不变")

    def _open_settings(self, snap_page: bool = False,
                       units_page: bool = False, text_page: bool = False):
        dlg = SettingsDialog(self.theme, self.canvas.snap_config, self,
                             units=self.units, precision=self.precision,
                             text_mode=self.text_mode,
                             text_scale=self.text_scale,
                             title_align=self.doc.align_title_text)
        if snap_page:
            dlg.show_snap_page()
        if units_page:
            dlg.show_units_page()
        if text_page:
            dlg.show_text_page()
        if dlg.exec() != QDialog.Accepted:
            return
        new_theme = dlg.theme()
        if new_theme != self.theme:
            self.set_theme(new_theme)
        self.canvas.set_snap_config(dlg.snap_config())
        units, precision = dlg.units_precision()
        if (units, precision) != (self.units, self.precision):
            self.set_units_precision(units, precision)
        new_mode, new_scale = dlg.text_settings()
        if (new_mode, new_scale) != (self.text_mode, self.text_scale):
            self.text_mode, self.text_scale = save_text_settings(
                new_mode, new_scale) if self._persist_snapping else (
                    new_mode, new_scale)
            self.doc.text_height_mode = self.text_mode
            self.doc.text_scale = self.text_scale
            self.canvas.fit_view()
            self.canvas.invalidate()
        if dlg.title_align_enabled() != self.doc.align_title_text:
            self._set_title_align(dlg.title_align_enabled())
        self._status(f"设置已保存：{theme_label(self.theme)}｜"
                     f"对象捕捉{'开' if self.canvas.snap_enabled else '关'}"
                     f"｜半径 {self.canvas.snap_config.radius_px:g} px｜"
                     f"{g.UNIT_LABELS.get(self.units, '单位')} · {self.precision} 位"
                     f"｜文字 {'原样 1:1' if self.text_mode == 'exact' else '自适应'}"
                     f" · {self.text_scale * 100:g}%")

    def _open_theme_settings(self):
        self._open_settings()

    def _open_snap_settings(self):
        self._open_settings(snap_page=True)

    def _open_units_settings(self):
        self._open_settings(units_page=True)

    def _open_text_settings(self):
        self._open_settings(text_page=True)

    def _toggle_dark(self):
        dark = self.canvas.toggle_dark()
        self._status(("已切换为黑底" if dark else "已切换为白底")
                     + "（仅画布；整体主题请在「主题设置」中修改）")

    def _on_document_edited(self):
        self.lblScale.setText(f"比例 {self.canvas.view.scale:.4g}")
