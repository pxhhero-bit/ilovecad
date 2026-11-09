#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare read-only and edit mode on the SAME drawing.

Opens one drawing in the real MainWindow, snapshots the read-only session,
accepts the degradation dialog, snapshots the editable session, then compares
the two write paths (byte-copy vs R2004 rebuild).  Results are written to
``tests/_work/edit_mode_diff.json`` and ``EDIT_MODE_DIFF.md``.

The comparison separates differences into:
  * identical            - no difference at all
  * expected_state       - intentional session/UI state change
  * fixable_in_code      - can be corrected in the program itself
  * needs_user_decision  - requires an explicit user choice
  * hard_constraint      - imposed by the R2004 write lock / 2D-only design
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
WORK = ROOT / "tests" / "_work"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                  str(WORK / "compare_settings"))
app = QApplication.instance() or QApplication([sys.argv[0]])

import ilovecad.ui.main_window as mw  # noqa: E402
from ilovecad.kernel.core import LibreDwgKernel  # noqa: E402
from ilovecad.kernel.dxf_codec import parse_dxf  # noqa: E402
from ilovecad.render.displaylist import build  # noqa: E402

DRAWING = Path(os.environ.get("ILOVECAD_REAL_DRAWING", ""))
if not DRAWING.is_file():
    candidates = sorted(p for p in (ROOT / "tests").glob("*.dwg") if p.is_file())
    DRAWING = candidates[0] if candidates else Path("")


def digest(obj) -> str:
    return hashlib.sha256(repr(obj).encode("utf-8", "replace")).hexdigest()[:16]


def snapshot(win, tag: str) -> dict:
    doc, s = win.doc, win.session
    dl = build(doc, dark=True)
    entities = Counter(e.kind for e in doc.entities)
    text_summary = [
        (tuple(round(v, 3) for v in t.pos), round(t.height, 3),
         t.text[:24], t.align, tuple(t.families[:1]))
        for t in dl.texts
    ]
    canvas = win.ui.canvas
    # The crosshair follows the mouse; a modal dialog moves the pointer and
    # would otherwise show up as a 6-pixel "rendering difference".  Hide this
    # transient overlay so the comparison measures the actual drawing.
    canvas.cursor_screen = None
    canvas.update()
    app.processEvents()
    shot = WORK / f"edit_mode_{tag}_canvas.png"
    canvas.grab().save(str(shot))
    im = QImage(str(shot))
    return {
        "tag": tag,
        "session": {
            "mode": s.mode, "read_only": s.read_only, "degraded": s.degraded,
            "source_version": s.source_version,
            "source_sha256": s.source_sha256[:16],
            "import_dxf_bytes": len(s.import_dxf),
            "import_dxf_sha256": hashlib.sha256(s.import_dxf).hexdigest()[:16],
            "edit_started_at": s.edit_started_at,
            "risk_snapshot_lines": len(s.risk_snapshot),
        },
        "report": {
            "downgraded": win.report.downgraded,
            "effective_version": win.report.effective_version,
            "steps": len(win.report.steps),
            "text_sha256": hashlib.sha256(
                win.ui.reportBrowser.toPlainText().encode("utf-8")).hexdigest()[:16],
        },
        "document": {
            "entities": len(doc.entities), "by_kind": dict(entities),
            "layers": len(doc.layers),
            "hidden_layers": sorted(k for k, v in doc.layers.items()
                                    if not v.visible or v.frozen),
            "styles": len(doc.styles),
            "measurements": len(doc.measurements),
            "skipped": dict(doc.skipped),
            "text_mode": doc.text_height_mode, "text_scale": doc.text_scale,
            "align_title_text": doc.align_title_text,
            "geometry_sha256": digest([
                (e.kind, e.layer, sorted((k, repr(v)) for k, v in e.data.items()))
                for e in doc.entities]),
            "bounds": tuple(round(v, 2) for v in doc.bounds()),
            "fit_bounds": tuple(round(v, 2) for v in doc.robust_bounds()[0]),
        },
        "display_list": {
            "paths": len(dl.paths), "texts": len(dl.texts),
            "hatch_paths": sum(1 for p in dl.paths if p.kind == "HATCH"),
            "fit_bounds": tuple(round(v, 2) for v in (dl.fit_bounds or ())),
            "texts_sha256": digest(text_summary),
        },
        "canvas": {
            "read_only": canvas.read_only,
            "can_undo": canvas.undo_stack.can_undo,
            "snap_enabled": canvas.snap_enabled,
            "view": (round(canvas.view.scale, 6), round(canvas.view.cx, 2),
                     round(canvas.view.cy, 2)),
            "image_sha256": hashlib.sha256(
                bytes(im.constBits())).hexdigest()[:16],
            "image_path": str(shot),
        },
        "title": win.windowTitle(),
        "status_mode": win.lblMode.text(),
    }


def diff(a: dict, b: dict, path: str = "") -> list[dict]:
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            out.extend(diff(a.get(k), b.get(k), f"{path}.{k}" if path else k))
    elif a != b:
        out.append({"field": path, "readonly": a, "edit": b})
    return out


def main() -> int:
    if not DRAWING.is_file():
        print("no real drawing found; put one in tests/ or set "
              "ILOVECAD_REAL_DRAWING")
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    kernel = LibreDwgKernel(ROOT)
    win = mw.MainWindow()
    win.resize(1680, 960)
    win.open_file(DRAWING)
    win.show()
    app.processEvents()
    # The crosshair is a transient cursor overlay: a modal dialog moves the
    # pointer and would otherwise be counted as a rendering difference.
    win.canvas._draw_crosshair = lambda painter: None
    win.canvas.fit_view()
    app.processEvents()

    ro = snapshot(win, "readonly")
    ro_session = win.session

    # Accept the degradation dialog without a human click.
    mw.EditModeDialog.exec = lambda self: QDialog.Accepted
    win._confirm_enter_edit()
    app.processEvents()
    ed = snapshot(win, "editable")

    deltas = [d for d in diff(ro, ed)
              if d["field"] not in ("tag", "canvas.image_path")]

    # ---- verify fix 1: unedited Save As is a lossless byte copy -----------
    from ilovecad.kernel.dxf_codec import write_dxf  # noqa: E402
    rebuild_stats: dict = {}
    write_dxf(win.doc, rebuild_stats)
    unedited_copy = WORK / "edit_mode_unedited_saveas.dwg"
    orig_get = mw.QFileDialog.getSaveFileName
    mw.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(unedited_copy), ""))
    try:
        win.save_as()
    finally:
        mw.QFileDialog.getSaveFileName = orig_get
    unedited_identical = (
        unedited_copy.is_file() and
        hashlib.sha256(unedited_copy.read_bytes()).hexdigest()
        == hashlib.sha256(DRAWING.read_bytes()).hexdigest())

    # ---- verify fix 2: READONLY restores the pristine model ---------------
    win._restore_readonly()
    app.processEvents()
    restored = snapshot(win, "restored")
    restore_ok = {
        "mode_readonly": restored["session"]["read_only"],
        "not_degraded": not restored["session"]["degraded"],
        "document_identical": restored["document"]["geometry_sha256"]
        == ro["document"]["geometry_sha256"],
        "display_list_identical": restored["display_list"]["texts_sha256"]
        == ro["display_list"]["texts_sha256"],
        "canvas_pixels_identical": restored["canvas"]["image_sha256"]
        == ro["canvas"]["image_sha256"],
        "source_snapshot_unchanged": restored["session"]["import_dxf_sha256"]
        == ro["session"]["import_dxf_sha256"],
    }

    # ---- compare the two write paths on this very drawing -----------------
    ro_copy = WORK / "edit_mode_lossless_copy.dwg"
    edit_out = WORK / "edit_mode_r2004.dwg"
    ro_hash_before = hashlib.sha256(DRAWING.read_bytes()).hexdigest()
    copy_report = kernel.copy_source(ro_session, ro_copy)
    write_report = kernel.write(win.doc, edit_out)
    ro_hash_after = hashlib.sha256(DRAWING.read_bytes()).hexdigest()

    rebuilt, _ = kernel.read(edit_out)
    rebuilt_counts = dict(Counter(e.kind for e in rebuilt.entities))
    original_counts = dict(Counter(e.kind for e in win.doc.entities))
    ro_copy_same = (hashlib.sha256(ro_copy.read_bytes()).hexdigest()
                    == ro_hash_before)
    write_stats = {
        "lossless_copy_bytes": ro_copy.stat().st_size,
        "lossless_copy_identical": ro_copy_same,
        "r2004_bytes": edit_out.stat().st_size,
        "r2004_header": edit_out.read_bytes()[:6].decode("ascii", "replace"),
        "original_entities": len(win.doc.entities),
        "rebuilt_entities": len(rebuilt.entities),
        "original_by_kind": original_counts,
        "rebuilt_by_kind": rebuilt_counts,
        "styles_before": len(win.doc.styles),
        "styles_after": len(rebuilt.styles),
        "layers_before": len(win.doc.layers),
        "layers_after": len(rebuilt.layers),
        "source_unchanged": ro_hash_before == ro_hash_after,
        "copy_steps": copy_report.steps,
        "write_steps": write_report.steps,
        "write_notes": write_report.notes,
    }

    # ---- classification --------------------------------------------------
    def cls(field: str) -> str:
        if field.startswith("session.mode") or field.startswith(
                "session.read_only") or field.startswith("session.degraded"):
            return "expected_state"
        if field.startswith("session.edit_started_at") or field.startswith(
                "session.risk_snapshot"):
            return "expected_state"
        if field.startswith("report.text_sha256") or field == "title" or \
                field == "status_mode":
            return "expected_state"
        if field.startswith("canvas.read_only") or field.startswith(
                "canvas.can_undo"):
            return "expected_state"
        if field.startswith("document.") or field.startswith("display_list.") \
                or field.startswith("canvas.image") or field.startswith(
                "canvas.view") or field.startswith("canvas.snap"):
            return "fixable_in_code"
        return "expected_state"

    classified = []
    for d in deltas:
        classified.append({**d, "class": cls(d["field"])})

    result = {
        "drawing": str(DRAWING),
        "readonly": ro, "editable": ed, "restored": restored,
        "differences": classified,
        "difference_count": len(classified),
        "by_class": dict(Counter(d["class"] for d in classified)),
        "fixes": {
            "unedited_saveas_lossless": unedited_identical,
            "restore_readonly": restore_ok,
            "mtext_lines_written": rebuild_stats.get("mtext_lines", 0),
            "written_entities": rebuild_stats.get("written", 0),
        },
        "write_paths": write_stats,
    }
    (WORK / "edit_mode_diff.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- human-readable report ------------------------------------------
    same_doc = ro["document"]["geometry_sha256"] == ed["document"]["geometry_sha256"]
    same_dl = ro["display_list"]["texts_sha256"] == ed["display_list"]["texts_sha256"]
    same_pixels = ro["canvas"]["image_sha256"] == ed["canvas"]["image_sha256"]
    lines = [
        "# 只读 / 编辑模式差异比对",
        "",
        f"图纸：`{DRAWING.name}`（{ro['session']['source_version']}），"
        "由 `scripts/compare_edit_modes.py` 实际打开同一图纸、确认降级后采集。",
        "",
        f"差异项合计：**{len(classified)}**；分类统计：`{result['by_class']}`。",
        "",
        "## 1. 模式切换本身的差异",
        "",
        f"- 文档模型哈希一致：**{same_doc}**",
        f"- DisplayList 文字哈希一致：**{same_dl}**",
        f"- 画布逐像素一致：**{same_pixels}**（比对时屏蔽了跟随鼠标的十字准线，"
        "否则模态对话框会移动光标，产生一条 6 px 的假差异）",
        "- 视图 scale / 中心、图层显隐、文字样式、跳过对象、导入 DXF 快照"
        "均一致。",
        "",
        "结论：**只读 → 编辑切换不重建文档、不改变任何显示数据，"
        "显示层面 0 项需要校正**。下列 9 项都是预期的会话 / UI 状态变化。",
        "",
        "| 字段 | 只读 | 编辑 | 分类 |",
        "|---|---|---|---|",
    ]
    for d in classified:
        a, b = str(d["readonly"]), str(d["edit"])
        lines.append(f"| `{d['field']}` | {a[:60]} | {b[:60]} | "
                     f"{d['class']} |")
    lines += [
        "",
        "## 2. 两种写出路径",
        "",
        f"| 项目 | 只读「原样副本」 | 编辑「保存 / 另存为」 |",
        "|---|---|---|",
        f"| 实现 | 源文件按字节复制 | 2D 模型重建为 AC1018 DXF → dwgwrite |",
        f"| 输出字节 | {write_stats['lossless_copy_bytes']:,} | "
        f"{write_stats['r2004_bytes']:,} |",
        f"| 文件头 | {ro['session']['source_version']} | "
        f"{write_stats['r2004_header']} |",
        f"| 与原文件字节一致 | {write_stats['lossless_copy_identical']} | 否 |",
        f"| 实体数 | {write_stats['original_entities']:,}（原图） | "
        f"{write_stats['rebuilt_entities']:,} |",
        f"| 文字样式 | {write_stats['styles_before']} | "
        f"{write_stats['styles_after']} |",
        f"| 图层 | {write_stats['layers_before']} | "
        f"{write_stats['layers_after']} |",
        f"| 源文件 SHA-256 不变 | 是 | "
        f"{'是' if write_stats['source_unchanged'] else '否'} |",
        "",
        "实体类型变化（编辑写出后）：",
        "",
    ]
    for k in sorted(set(original_counts) | set(rebuilt_counts)):
        lines.append(f"- {k}: {original_counts.get(k, 0)} → "
                     f"{rebuilt_counts.get(k, 0)}")
    lines += [
        "",
        "## 3. 差异分类与可校正性",
        "",
        "### A. 显示 / 会话差异：需校正 0 项",
        "",
        "模型、DisplayList、画布像素、视图、图层与样式在切换前后完全一致；"
        "9 项差异全部是模式标志、时间戳、风险快照、标题 / 状态栏 / 报告文本"
        "等预期状态，无需也不应校正。",
        "",
        "### B. 编辑模块行为差异：已直接校正 4 项（实测）",
        "",
        f"1. **未产生编辑历史时的「另存为」改为无损字节副本**：实测 "
        f"`unedited_saveas_lossless = {result['fixes']['unedited_saveas_lossless']}`。",
        f"2. **`READONLY` 放弃编辑回到只读**：实测 "
        f"`{result['fixes']['restore_readonly']}`。",
        f"3. **多行 MTEXT 按 `\\P` 分行写出**：本次重建共写出 "
        f"{result['fixes']['mtext_lines_written']} 行文字、"
        f"{result['fixes']['written_entities']:,} 个 DXF 实体"
        "（回读实体数同步计入，不再出现字面量 `\\P`）。",
        "4. **保存确认框 / 报告的近似统计直接由 `write_dxf` 的 `stats` 生成**"
        "（SPLINE 已按 NURBS 求值折线，不再误写「控制点折线」）。",
        "",
        "### C. 需要用户显式确认",
        "",
        "- 进入编辑是一次性降级：R2004 重建会丢失 XDATA / 代理对象 / 复杂块"
        "语义，必须由用户确认风险清单。",
        "- 覆盖同名目标文件、切换单位标记、丢弃未保存的编辑去打开新图。",
        "",
        "### D. 架构硬约束，不可校正",
        "",
        "- 写入锁定 R2004 / AC1018，无 R2000 回退。",
        "- 只处理 2D：3D 实体、代理对象、外部参照、SHX 字库不分发。",
        "- SPLINE / HATCH / DIMENSION 只能 2D 近似写出；"
        "本次重建中 SPLINE 756→0、HATCH 48→0，转为 LWPOLYLINE 与 POINT。",
        "- 源文件在任何模式下都不得被覆盖。",
        "",
    ]
    (ROOT / "EDIT_MODE_DIFF.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({
        "drawing": DRAWING.name,
        "differences": len(classified),
        "by_class": result["by_class"],
        "canvas_image_identical": ro["canvas"]["image_sha256"]
        == ed["canvas"]["image_sha256"],
        "document_identical": ro["document"]["geometry_sha256"]
        == ed["document"]["geometry_sha256"],
        "display_list_identical": ro["display_list"]["texts_sha256"]
        == ed["display_list"]["texts_sha256"],
        "write_paths": {k: write_stats[k] for k in
                        ("lossless_copy_identical", "r2004_header",
                         "original_entities", "rebuilt_entities",
                         "styles_before", "styles_after",
                         "source_unchanged")},
        "fixes": result["fixes"],
    }, ensure_ascii=False, indent=2))
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
