# SPDX-License-Identifier: GPL-3.0-or-later
"""Layer manager dialog: create / rename / delete / style / lock 2D layers."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QInputDialog, QMessageBox,
                               QTableWidgetItem)

from ..model import layer_ops
from ..model.document import DwgDocument
from ..render.colors import aci_rgb
from ..tools.commands import UndoStack
from .loader import load_ui

COL_VISIBLE, COL_FROZEN, COL_LOCKED, COL_CURRENT = 0, 1, 2, 3
COL_NAME, COL_COLOR, COL_LTYPE, COL_LW, COL_COUNT = 4, 5, 6, 7, 8


class LayerManagerDialog(QDialog):
    def __init__(self, doc: DwgDocument, current_layer: str = "0",
                 undo_stack: UndoStack | None = None, parent=None):
        super().__init__(parent)
        self.ui = load_ui(self, "layer_manager")
        self.doc = doc
        self.undo_stack = undo_stack
        self.current_layer_name = current_layer if current_layer in doc.layers else "0"
        self.changed = 0
        self._loading = False
        t = self.ui.layerTable
        t.setEditTriggers(t.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(t.SelectionBehavior.SelectRows)
        t.setAlternatingRowColors(True)
        t.verticalHeader().setVisible(False)
        t.itemChanged.connect(self._on_item_changed)
        t.cellDoubleClicked.connect(self._on_double_click)
        t.cellClicked.connect(self._on_click)
        self.ui.btnNewLayer.clicked.connect(self.new_layer)
        self.ui.btnRenameLayer.clicked.connect(self.rename_layer)
        self.ui.btnDeleteLayer.clicked.connect(self.delete_layer)
        self.ui.btnSetCurrent.clicked.connect(self.set_current)
        self.ui.btnAllOn.clicked.connect(self.all_on)
        self.ui.btnAllUnlocked.clicked.connect(self.all_unlocked)
        self.ui.btnClose.clicked.connect(self.accept)
        self._reload()

    # ------------------------------------------------------------------ table
    def _reload(self) -> None:
        self._loading = True
        t = self.ui.layerTable
        names = sorted(self.doc.layers)
        t.setRowCount(len(names))
        usage = layer_ops.layer_usage(self.doc)
        for row, name in enumerate(names):
            layer = self.doc.layers[name]
            for col, on in ((COL_VISIBLE, layer.visible and not layer.frozen),
                            (COL_FROZEN, layer.frozen),
                            (COL_LOCKED, layer.locked)):
                item = QTableWidgetItem("")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if on
                                   else Qt.CheckState.Unchecked)
                t.setItem(row, col, item)
            current = QTableWidgetItem("●" if name == self.current_layer_name else "")
            current.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setItem(row, COL_CURRENT, current)
            t.setItem(row, COL_NAME, QTableWidgetItem(name))
            color = QTableWidgetItem(f"ACI {layer.color}")
            color.setForeground(QColor(*aci_rgb(layer.color)))
            t.setItem(row, COL_COLOR, color)
            t.setItem(row, COL_LTYPE, QTableWidgetItem(layer.linetype))
            t.setItem(row, COL_LW, QTableWidgetItem(f"{layer.lineweight:.2f}"))
            count = QTableWidgetItem(str(usage.get(name, 0)))
            count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            t.setItem(row, COL_COUNT, count)
        for col in (COL_VISIBLE, COL_FROZEN, COL_LOCKED, COL_CURRENT):
            t.resizeColumnToContents(col)
        t.resizeColumnToContents(COL_NAME)
        self._loading = False

    def _name_at(self, row: int) -> str | None:
        item = self.ui.layerTable.item(row, COL_NAME)
        return item.text() if item else None

    def _selected_name(self) -> str | None:
        row = self.ui.layerTable.currentRow()
        return self._name_at(row) if row >= 0 else None

    def current_layer(self) -> str:
        return self.current_layer_name

    # ----------------------------------------------------------------- helpers
    def _apply(self, label: str, fn) -> bool:
        """Wrap a layer edit in the shared undo stack when one is available."""
        try:
            if self.undo_stack is not None:
                ok = self.undo_stack.perform(label, fn)
                if not ok:
                    return False
            else:
                fn()
        except ValueError as exc:
            QMessageBox.warning(self, "图层操作失败", str(exc))
            return False
        self.changed += 1
        self._reload()
        return True

    def _on_item_changed(self, item) -> None:
        if self._loading:
            return
        name = self._name_at(item.row())
        if name is None:
            return
        on = item.checkState() == Qt.CheckState.Checked
        col = item.column()
        if col == COL_VISIBLE:
            self._apply(f"图层 {name} 显示状态", lambda: layer_ops.set_flags(
                self.doc, name, visible=on, frozen=False if on else None))
        elif col == COL_FROZEN:
            self._apply(f"图层 {name} 冻结状态", lambda: layer_ops.set_flags(
                self.doc, name, frozen=on))
        elif col == COL_LOCKED:
            self._apply(f"图层 {name} 锁定状态", lambda: layer_ops.set_flags(
                self.doc, name, locked=on))

    def _on_click(self, row: int, col: int) -> None:
        if col == COL_CURRENT:
            name = self._name_at(row)
            if name:
                self.current_layer_name = name
                self._reload()

    # ----------------------------------------------------------------- actions
    def new_layer(self) -> None:
        text, ok = QInputDialog.getText(self, "新建图层", "图层名：")
        if not ok:
            return
        try:
            name = layer_ops.valid_name(text)
        except ValueError as exc:
            QMessageBox.warning(self, "图层操作失败", str(exc))
            return
        if self._apply(f"新建图层 {name}", lambda: layer_ops.create_layer(self.doc, name)):
            self.current_layer_name = name
            self._reload()

    def rename_layer(self) -> None:
        old = self._selected_name()
        if old is None:
            return
        text, ok = QInputDialog.getText(self, "重命名图层", "新图层名：", text=old)
        if not ok:
            return
        try:
            new = layer_ops.valid_name(text)
        except ValueError as exc:
            QMessageBox.warning(self, "图层操作失败", str(exc))
            return
        if self._apply(f"重命名图层 {old} → {new}",
                       lambda: layer_ops.rename_layer(self.doc, old, new)):
            if self.current_layer_name == old:
                self.current_layer_name = new
            self._reload()

    def delete_layer(self) -> None:
        name = self._selected_name()
        if name is None:
            return
        count = layer_ops.layer_usage(self.doc).get(name, 0)
        msg = f"确定删除图层 {name} 吗？"
        if count:
            msg += f"\n其上的 {count} 个 2D 实体会迁移到图层 0（不会删除实体）。"
        if QMessageBox.question(self, "删除图层", msg,
                                QMessageBox.StandardButton.Yes |
                                QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != \
                QMessageBox.StandardButton.Yes:
            return
        if self._apply(f"删除图层 {name}",
                       lambda: layer_ops.delete_layer(self.doc, name)):
            if self.current_layer_name == name:
                self.current_layer_name = "0"
            self._reload()

    def set_current(self) -> None:
        name = self._selected_name()
        if name:
            self.current_layer_name = name
            self._reload()

    def all_on(self) -> None:
        self._apply("全部图层显示", lambda: layer_ops.all_on(self.doc))

    def all_unlocked(self) -> None:
        def fn():
            for name in self.doc.layers:
                layer_ops.set_flags(self.doc, name, locked=False)
        self._apply("全部图层解锁", fn)

    def _on_double_click(self, row: int, col: int) -> None:
        name = self._name_at(row)
        if name is None:
            return
        if col == COL_NAME:
            self.ui.layerTable.selectRow(row)
            self.rename_layer()
            return
        if col == COL_COLOR:
            layer = self.doc.layers[name]
            value, ok = QInputDialog.getInt(
                self, "图层颜色", "ACI 颜色号 (1–255)：", layer.color, 1, 255)
            if ok:
                self._apply(f"图层 {name} 颜色", lambda: layer_ops.set_style(
                    self.doc, name, color=value))
        elif col == COL_LTYPE:
            layer = self.doc.layers[name]
            choices = list(layer_ops.LINETYPES)
            index = choices.index(layer.linetype) if layer.linetype in choices else 0
            value, ok = QInputDialog.getItem(
                self, "图层线型", "选择线型：", choices, index, False)
            if ok:
                self._apply(f"图层 {name} 线型", lambda: layer_ops.set_style(
                    self.doc, name, linetype=value))
        elif col == COL_LW:
            layer = self.doc.layers[name]
            value, ok = QInputDialog.getDouble(
                self, "图层线宽", "线宽 (mm，0 = 使用默认 0.25)：",
                layer.lineweight, 0.0, 2.11, 2)
            if ok:
                self._apply(f"图层 {name} 线宽", lambda: layer_ops.set_style(
                    self.doc, name, lineweight=value))
