# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-destructive batch conversion to R2004 DWG / AC1018 DXF.

Every output gets a fresh name; source files are never opened for writing and
an existing output is never overwritten.  High-version DWGs go through the
same source-side downgrade path as a normal open, and skipped 3D / unsupported
objects are written into the log instead of being silently dropped.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QMessageBox,
                               QListWidgetItem)

from ..kernel.core import KernelError, LibreDwgKernel
from ..version import TARGET_VERSION
from .loader import load_ui

FILE_FILTER = "DWG / DXF 图纸 (*.dwg *.dxf);;所有文件 (*)"
SOURCE_SUFFIXES = (".dwg", ".dxf")


def unique_target(out_dir: Path, source: Path, mode: str) -> Path:
    """Build a never-overwriting output path next to the chosen directory."""
    tag, ext = ("R2004", ".dwg") if mode == "dwg" else ("AC1018", ".dxf")
    base = out_dir / f"{source.stem}_{tag}{ext}"
    if not base.exists():
        return base
    i = 1
    while True:
        candidate = out_dir / f"{source.stem}_{tag}_{i}{ext}"
        if not candidate.exists():
            return candidate
        i += 1


class BatchConvertDialog(QDialog):
    def __init__(self, kernel: LibreDwgKernel, parent=None,
                 files: list[Path] | None = None,
                 output_dir: Path | None = None, mode: str = "dwg"):
        super().__init__(parent)
        self.ui = load_ui(self, "batch_convert")
        self.kernel = kernel
        self._running = False
        self._cancel = False
        self.ui.btnAddFiles.clicked.connect(self._add_files)
        self.ui.btnAddFolder.clicked.connect(self._add_folder)
        self.ui.btnRemoveSelected.clicked.connect(self._remove_selected)
        self.ui.btnClearList.clicked.connect(self.ui.fileList.clear)
        self.ui.btnBrowseOutput.clicked.connect(self._browse_output)
        self.ui.btnStart.clicked.connect(self._start)
        self.ui.btnClose.clicked.connect(self._close)
        self._add_paths(files or [])
        if output_dir:
            self.ui.outputDir.setText(str(output_dir))
        self.ui.comboFormat.setCurrentIndex(0 if mode == "dwg" else 1)
        self._log("就绪。添加文件、选择输出目录后点击「开始转换」。")

    # ------------------------------------------------------------------ input
    def _log(self, text: str) -> None:
        self.ui.logText.appendPlainText(text)
        QApplication.processEvents()

    def _add_paths(self, paths) -> None:
        existing = {self.ui.fileList.item(i).text()
                    for i in range(self.ui.fileList.count())}
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() not in SOURCE_SUFFIXES or not p.is_file():
                continue
            text = str(p)
            if text in existing:
                continue
            existing.add(text)
            self.ui.fileList.addItem(QListWidgetItem(text))

    def _add_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "选择待转换图纸", "", FILE_FILTER)
        self._add_paths(names)

    def _add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择包含图纸的目录")
        if folder:
            self._add_paths(sorted(Path(folder).rglob("*")))
            if not self.ui.outputDir.text().strip():
                self.ui.outputDir.setText(str(Path(folder) / "converted_r2004"))

    def _remove_selected(self) -> None:
        for item in list(self.ui.fileList.selectedItems()):
            self.ui.fileList.takeItem(self.ui.fileList.row(item))

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if folder:
            self.ui.outputDir.setText(folder)

    # --------------------------------------------------------------- conversion
    def _close(self) -> None:
        if self._running:
            self._cancel = True
            self._log("已请求停止，将在当前文件完成后结束。")
            return
        self.reject()

    def _start(self) -> None:
        files = [Path(self.ui.fileList.item(i).text())
                 for i in range(self.ui.fileList.count())]
        out_dir = Path(self.ui.outputDir.text().strip())
        if not files:
            QMessageBox.information(self, "批量转换", "请先添加待转换的 DWG / DXF 文件。")
            return
        if not self.ui.outputDir.text().strip():
            QMessageBox.information(self, "批量转换", "请先选择输出目录。")
            return
        mode = "dwg" if self.ui.comboFormat.currentIndex() == 0 else "dxf"
        result = self.run_conversion(files, out_dir, mode)
        QMessageBox.information(
            self, "批量转换完成",
            f"成功 {result['ok']} 个，失败 {result['failed']} 个"
            + (f"，取消 {result['cancelled']} 个" if result["cancelled"] else "")
            + "。\n明细见日志；原文件均未改写。")

    def run_conversion(self, files: list[Path], out_dir: Path,
                       mode: str = "dwg") -> dict:
        """Convert every file, never touching a source or an existing output."""
        if self._running:
            return {"ok": 0, "failed": 0, "cancelled": 0, "targets": []}
        self._running, self._cancel = True, False
        self.ui.btnStart.setEnabled(False)
        self.ui.progressBar.setMaximum(max(len(files), 1))
        self.ui.progressBar.setValue(0)
        result = {"ok": 0, "failed": 0, "cancelled": 0, "targets": []}
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, source in enumerate(files):
                if self._cancel:
                    result["cancelled"] += 1
                    self._log(f"已取消：{source.name}")
                    continue
                self.ui.progressBar.setValue(i)
                try:
                    doc, report = self.kernel.read(source)
                    target = unique_target(out_dir, source, mode)
                    if mode == "dwg":
                        self.kernel.write(doc, target)
                        version = LibreDwgKernel.detect_version(target)
                        if version != TARGET_VERSION:
                            raise KernelError(f"写出版本校验失败：{version or '未知'}")
                    else:
                        self.kernel.export_dxf(doc, target)
                        if b"AC1018" not in target.read_bytes()[:4096]:
                            raise KernelError("DXF 头部缺少 AC1018 版本标记")
                    notes = []
                    if report.downgraded:
                        notes.append(f"源头降版本 {report.source_version} → {TARGET_VERSION}")
                    skipped = doc.skipped or {}
                    if skipped:
                        notes.append("跳过 " + "、".join(
                            f"{k}×{v}" for k, v in sorted(skipped.items())[:8]))
                    if report.warnings:
                        notes.append(f"警告 {len(report.warnings)} 条")
                    self._log(
                        f"[{i + 1}/{len(files)}] 成功 {source.name} → {target.name}"
                        f"｜{len(doc.entities)} 个 2D 实体"
                        + ("｜" + "；".join(notes) if notes else ""))
                    result["ok"] += 1
                    result["targets"].append(target)
                except (KernelError, OSError, ValueError) as exc:
                    result["failed"] += 1
                    self._log(f"[{i + 1}/{len(files)}] 失败 {source.name}：{exc}")
                finally:
                    QApplication.processEvents()
            self.ui.progressBar.setValue(len(files))
        finally:
            self._running = False
            self.ui.btnStart.setEnabled(True)
            self._log(f"完成：成功 {result['ok']}，失败 {result['failed']}，"
                      f"取消 {result['cancelled']}。输出目录：{out_dir}")
        return result
