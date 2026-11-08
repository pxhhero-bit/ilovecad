#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless end-to-end self test with an offscreen Qt window screenshot."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ilovecad.kernel.core import LibreDwgKernel  # noqa: E402
from ilovecad.kernel.dxf_codec import write_dxf  # noqa: E402
from ilovecad.model.document import DwgDocument  # noqa: E402
from ilovecad.model.entities import Entity  # noqa: E402
from ilovecad.preferences import load_snap_config, save_snap_config  # noqa: E402
from ilovecad.render.displaylist import build  # noqa: E402
from ilovecad.render.pdf import PdfOptions, PdfRenderer  # noqa: E402
from ilovecad.tools import measure, snapping as snap  # noqa: E402
from ilovecad.ui.batch_convert import BatchConvertDialog, unique_target  # noqa: E402
from ilovecad.ui.main_window import MainWindow, SettingsDialog  # noqa: E402
from ilovecad.version import TARGET_VERSION  # noqa: E402

WORK = ROOT / "tests" / "_work"
SHOTS = ROOT / "screenshots"


def step(msg: str) -> None:
    print(f"[selftest] {msg}", flush=True)


def make_document() -> DwgDocument:
    d = DwgDocument()
    d.add(Entity(0, "LINE", "WALL", data={"a": (0.0, 0.0), "b": (120.0, 0.0)}))
    d.add(Entity(0, "LINE", "WALL", data={"a": (120.0, 0.0), "b": (120.0, 80.0)}))
    d.add(Entity(0, "LWPOLYLINE", "WALL", data={
        "pts": [(0.0, 0.0, 0.0), (0.0, 80.0, 0.0), (120.0, 80.0, 0.0),
                (120.0, 0.0, 0.0)], "closed": True}))
    d.add(Entity(0, "CIRCLE", "AXIS", data={"c": (60.0, 40.0), "r": 22.0}))
    d.add(Entity(0, "ARC", "AXIS", data={"c": (60.0, 40.0), "r": 30.0,
                                         "start": 20.0, "end": 160.0}))
    d.add(Entity(0, "TEXT", "NOTE", data={"p": (8.0, 92.0), "text": "IloveCAD 2D 测试图",
                                          "height": 5.0, "rotation": 0.0, "width": 0.0}))
    d.layers["WALL"].color = 7
    d.layers["AXIS"].color = 1
    d.layers["NOTE"].color = 5
    d.measurements.append({"kind": "距离", "value": 120.0, "label": "120.0000 mm",
                           "points": [(0.0, -8.0), (120.0, -8.0)], "closed": False,
                           "extra": ""})
    return d


def snap_checks() -> dict:
    """Typed snapping: boundary points, midpoints, centres, intersections."""
    d = DwgDocument()
    d.add(Entity(0, "LINE", "0", data={"a": (0.0, 0.0), "b": (100.0, 0.0)}))
    d.add(Entity(0, "CIRCLE", "0", data={"c": (50.0, 0.0), "r": 20.0}))
    d.add(Entity(0, "LWPOLYLINE", "0", data={
        "pts": [(200.0, 0.0, 0.0), (300.0, 0.0, 0.0),
                (300.0, 100.0, 0.0), (200.0, 100.0, 0.0)], "closed": True}))
    d.add(Entity(0, "ARC", "0", data={"c": (400.0, 0.0), "r": 30.0,
                                      "start": 0.0, "end": 90.0}))
    d.add(Entity(0, "LINE", "0", data={"a": (40.0, -50.0), "b": (40.0, 50.0)}))

    def probe(p, kinds=None, reference=None, tol=2.0, enabled=True):
        cfg = snap.SnapConfig(
            enabled=enabled,
            modes={k: (k in kinds if kinds is not None else True)
                   for k in snap.SNAP_KINDS})
        return snap.find_snap(d, p, tol, True, cfg, reference)

    def kind(p, kinds, reference=None):
        r = probe(p, kinds, reference)
        return r.kind if r else None

    results = {
        "endpoint": kind((0.3, 0.2), ["endpoint"]),
        "midpoint": kind((50.0, 0.4), ["midpoint"]),
        "center_circle": kind((50.2, 0.1), ["center"]),
        "center_polygon": kind((250.0, 50.0), ["center"]),
        "center_closed_bounds": probe((250.0, 50.0), ["center"]).point,
        "quadrant": kind((70.0, 0.3), ["quadrant"]),
        "arc_endpoint": kind((430.0, 0.2), ["endpoint"]),
        "arc_midpoint": kind((421.213, 21.213), ["midpoint"]),
        "intersection": kind((40.2, 0.2), ["intersection"]),
        "perpendicular": kind((40.2, 0.2), ["perpendicular"], (40.0, 10.0)),
        "nearest": kind((25.0, 1.0), ["nearest"]),
        "priority_endpoint_over_nearest": kind((0.2, 0.2), None),
        "all_modes_off": kind((0.2, 0.2), []),
        "global_off": probe((0.2, 0.2), None, enabled=False),
        # A circle is drawn with 72 chords; those must NOT become fake
        # endpoints/midpoints that drown out the real centre snap.
        "no_tessellation_fake_points": probe(
            (50.0 + 20.0 * 0.8660254, 10.0),
            ["endpoint", "midpoint", "quadrant", "center"]),
        "radius_clamped": snap.SnapConfig(radius_px=999).radius_px,
    }
    assert results["endpoint"] == "endpoint", "boundary/endpoint snap failed"
    assert results["midpoint"] == "midpoint", "midpoint snap failed"
    assert results["center_circle"] == "center", "circle centre snap failed"
    assert results["center_polygon"] == "center", "polygon centre snap failed"
    assert abs(results["center_closed_bounds"][0] - 250.0) < 1e-6
    assert results["quadrant"] == "quadrant", "quadrant snap failed"
    assert results["arc_endpoint"] == "endpoint", "arc endpoint snap failed"
    assert results["arc_midpoint"] == "midpoint", "arc midpoint snap failed"
    assert results["intersection"] == "intersection", "intersection snap failed"
    assert results["perpendicular"] == "perpendicular", "perpendicular snap failed"
    assert results["nearest"] == "nearest", "nearest snap failed"
    assert results["priority_endpoint_over_nearest"] == "endpoint", "snap priority failed"
    assert results["all_modes_off"] is None
    assert results["global_off"] is None
    assert results["no_tessellation_fake_points"] is None
    assert results["radius_clamped"] == 40.0
    return {k: v for k, v in results.items() if not isinstance(v, snap.SnapResult)}


def redteam_checks(kernel: LibreDwgKernel, result: dict) -> dict:
    """Regression checks for the defects a real-world drawing exposed."""
    from ilovecad.kernel.core import KernelError
    from ilovecad.kernel.dxf_codec import _decode, parse_dxf, write_dxf
    from ilovecad.tools.commands import UndoStack
    from ilovecad.ui.main_window import MainWindow

    checks: dict = {}

    step("红队回归：DXF 编码容错（一处坏字节不得毁掉全图中文）")
    raw = ("0\nLAYER\n2\n粗实线\n0\nEOF\n").encode("utf-8") + b"\xe7\n"
    text, repaired = _decode(raw)
    assert "粗实线" in text, "isolated bad byte degraded the whole DXF to mojibake"
    assert repaired >= 1
    checks["encoding_repaired"] = repaired

    step("红队回归：写出统计、bulge 保真、未知类型拒绝、源单位不改写")
    d = DwgDocument()
    d.add(Entity(0, "LWPOLYLINE", "0", data={
        "pts": [(0.0, 0.0, 0.5), (10.0, 0.0, 0.0)], "closed": False}))
    d.add(Entity(0, "HATCH", "0", data={
        "paths": [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)],
                  [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0)]]}))
    d.add(Entity(0, "3DFACE", "0", data={"pts": [(0, 0), (1, 0), (1, 1)]}))
    d.source_units = 0
    d.units = 4
    stats: dict = {}
    payload = write_dxf(d, stats)
    assert stats["written"] == 3, stats
    assert stats["source"] == 3 and stats["skipped"]["3DFACE"] == 1, stats
    assert stats["converted"]["HATCH→LWPOLYLINE"] == 1, stats
    assert b"$INSUNITS\n70\n0\n" in payload, "source $INSUNITS was relabelled"
    assert b"42\n0.500000\n" in payload, "polyline bulge was dropped"
    back, _ = parse_dxf(payload)
    assert len(back.entities) == 3 and len(back.layers) >= 1
    assert back.entities[0].data["pts"][0][2] == 0.5
    blocked = WORK / "redteam_must_not_exist.dwg"
    blocked.unlink(missing_ok=True)
    try:
        kernel.write(d, blocked)
        raise AssertionError("unknown entity type was silently written")
    except KernelError as exc:
        assert "拒绝" in str(exc)
    assert not blocked.exists(), "a rejected write still touched the target"
    checks["writer_stats"] = {"written": stats["written"],
                              "converted": dict(stats["converted"]),
                              "skipped": dict(stats["skipped"])}

    step("红队回归：大图纸撤销栈使用紧凑快照与动态上限")
    big = DwgDocument()
    for i in range(5001):
        big.add(Entity(0, "POINT", "0", data={"p": (float(i), 0.0)}))
    stack = UndoStack(big)
    assert stack._effective_limit() == 20
    old_color = big.entities[0].color
    assert stack.perform("test", lambda: setattr(big.entities[0], "color", 3))
    assert isinstance(stack._undo[-1].before, bytes)
    stack.undo()
    assert big.entities[0].color == old_color
    stack.redo()
    assert big.entities[0].color == 3
    checks["undo"] = {"limit": stack._effective_limit(),
                      "snapshot_bytes": len(stack._undo[-1].before)}

    step("红队回归：源文件保护")
    probe = MainWindow()
    probe.doc.downgraded = True
    probe.doc.source_version = "AC1032"
    probe.current_path = WORK / "source.dxf"
    assert "DXF" in probe._source_protect_reason()
    probe.current_path = WORK / "source.dwg"
    assert "高版本" in probe._source_protect_reason()
    probe.doc.downgraded = False
    probe.doc.source_version = TARGET_VERSION
    assert probe._source_protect_reason() == ""
    risks = probe._save_risk_lines()

    step("红队回归：图层管理 / 属性编辑 / 镜像缩放 / 单位设置页")
    from ilovecad.model import layer_ops
    from ilovecad.tools import properties
    from ilovecad.ui.layer_manager import LayerManagerDialog
    from ilovecad.ui.main_window import SettingsDialog
    import ilovecad.ui.main_window as mw

    ed = DwgDocument()
    le = ed.add(Entity(0, "LINE", "WALL",
                       data={"a": (0.0, 0.0), "b": (10.0, 0.0)}))
    ce = ed.add(Entity(0, "CIRCLE", "AXIS",
                       data={"c": (5.0, 5.0), "r": 2.0}))
    le.selected = ce.selected = True
    layer_ops.create_layer(ed, "TEMP", color=3)
    assert "TEMP" in ed.layers
    assert layer_ops.rename_layer(ed, "TEMP", "TEMP2") == 0
    assert len(properties.rows(ed, ed.entities)) >= 6
    assert properties.apply_value(ed, [ce], "data:r", "3.5") == 1
    assert ce.data["r"] == 3.5
    layer_ops.set_flags(ed, "AXIS", locked=True)
    assert properties.editable_entities(ed, [ce]) == []
    assert le.clone().transform("mirror", (0.0, 0.0), (0.0, 1.0)).data["b"] == (-10.0, 0.0)
    scaled = ce.clone().transform("scale", (0.0, 0.0), 2.0)
    assert scaled.data["c"] == (10.0, 10.0)
    assert scaled.data["r"] == ce.data["r"] * 2.0
    assert le.clone().transform("scale", (0.0, 0.0), 2.0, 3.0).data["b"] == (20.0, 0.0)
    layer_ops.set_flags(ed, "AXIS", locked=False)
    lm = LayerManagerDialog(ed, "0", UndoStack(ed))
    assert hasattr(lm.ui, "layerTable")
    lm.close()
    sd = SettingsDialog(probe.theme, probe.canvas.snap_config, probe)
    assert sd.ui.tabSettings.count() == 3
    units, precision = sd.units_precision()
    assert isinstance(units, int) and isinstance(precision, int)
    sd.close()

    # A protected source must never be overwritten by Ctrl+S: save() explains
    # and redirects to Save As. Patch away the modal dialogs.
    protected = WORK / "protected_source.dwg"
    protected.write_bytes(b"AC1032-ORIGINAL")
    digest = hashlib.sha256(protected.read_bytes()).hexdigest()
    probe.current_path = protected
    probe.doc.downgraded = True
    probe.doc.source_version = "AC1032"
    info_calls: list[str] = []
    orig_info = mw.QMessageBox.information
    orig_save = mw.QFileDialog.getSaveFileName
    mw.QMessageBox.information = staticmethod(
        lambda *a, **k: info_calls.append("protected"))
    mw.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    try:
        probe.save()
    finally:
        mw.QMessageBox.information = orig_info
        mw.QFileDialog.getSaveFileName = orig_save
    assert info_calls == ["protected"], "source protection did not engage"
    assert hashlib.sha256(protected.read_bytes()).hexdigest() == digest
    probe.close()
    checks["source_protected"] = True
    checks["edit_features"] = True

    step("真实图纸（可选）：高版本 / 中文 / 往返 / 原文件不变")
    real = Path(os.environ.get("ILOVECAD_REAL_DRAWING", ""))
    if not real.is_file():
        candidates = sorted(p for p in (ROOT / "tests").glob("*.dwg")
                            if p.is_file())
        real = candidates[0] if candidates else Path("")
    if not real.is_file():
        checks["realworld"] = "skipped (no drawing in tests/)"
        result["redteam"] = checks
        return checks

    before = hashlib.sha256(real.read_bytes()).hexdigest()
    doc, report = kernel.read(real)
    layer_names = "\n".join(doc.layers)
    text_values = "\n".join(str(e.data.get("text", ""))
                            for e in doc.entities if e.kind == "TEXT")
    assert not any(tag in layer_names + text_values
                   for tag in ("Ã", "ä¸", "å®", "æ ")), "UTF-8 mojibake survived"
    assert doc.source_units is not None
    assert len(doc.entities) > 100 and len(doc.layers) > 1
    target = WORK / "realworld_r2004.dwg"
    saved = kernel.write(doc, target)
    assert target.read_bytes()[:6].decode("ascii") == TARGET_VERSION
    assert "written" in saved.counts
    back_doc, _ = kernel.read(target)
    # HATCH loops are each written as a polyline, so the output entity count
    # can legitimately exceed the in-memory entity count. Compare against the
    # writer's own honest count instead, which the kernel already re-verified.
    assert len(back_doc.entities) == saved.counts["written"], \
        "round-trip entity count differs from what was written"
    assert len(back_doc.entities) >= len(doc.entities), "round-trip lost entities"
    assert set(back_doc.layers) == set(doc.layers), "round-trip lost layers"
    assert not [e for e in doc.entities if e.kind == "HATCH"
                and any(abs(p[0]) < 1e-9 and abs(p[1]) < 1e-9
                        for path in e.data.get("paths", []) for p in path)], \
        "HATCH elevation point produced a fake full-sheet diagonal"
    assert hashlib.sha256(real.read_bytes()).hexdigest() == before
    dl = build(back_doc)
    real_pdf = WORK / "realworld.pdf"
    PdfRenderer.export(str(real_pdf), back_doc, dl, PdfOptions(paper="A3"), None)
    assert real_pdf.read_bytes()[:5] == b"%PDF-"
    checks["realworld"] = {
        "file": real.name, "source_version": report.source_version,
        "downgraded": report.downgraded, "entities": len(doc.entities),
        "layers": len(doc.layers), "source_units": doc.source_units,
        "warnings": report.warnings,
        "reread_entities": len(back_doc.entities),
        "saved_notes": saved.notes[:4], "pdf_bytes": real_pdf.stat().st_size,
        "source_unchanged": True,
    }
    result["redteam"] = checks
    return checks


def readonly_checks(kernel: LibreDwgKernel, result: dict) -> dict:
    """Read-only source layer / explicit degradation regression."""
    from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

    from ilovecad.kernel.core import KernelError
    import ilovecad.ui.main_window as mw

    source = WORK / "selftest_r2004.dwg"
    assert source.is_file(), "run the R2004 write test first"
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    # ---------------------------------------------------------- kernel layer
    session = kernel.read_session(source)
    assert session.read_only and session.source_sha256 == before
    assert session.import_dxf and session.document.entities
    assert "源文件" in "\n".join(session.risk_lines())
    lossless = WORK / "readonly_copy.dwg"
    report = kernel.copy_source(session, lossless)
    assert hashlib.sha256(lossless.read_bytes()).hexdigest() == before
    assert "按字节复制" in report.to_text()
    for bad in (source, WORK / "readonly_copy.dxf"):
        try:
            kernel.copy_source(session, bad)
            raise AssertionError("copy_source accepted an unsafe target")
        except KernelError:
            pass
    volatile = WORK / "readonly_volatile.dwg"
    volatile.write_bytes(source.read_bytes())
    vs = kernel.read_session(volatile)
    volatile.write_bytes(volatile.read_bytes() + b"EXTERNAL-CHANGE")
    try:
        kernel.copy_source(vs, WORK / "readonly_volatile_copy.dwg")
        raise AssertionError("external source change was not detected")
    except KernelError:
        pass
    volatile.write_bytes(source.read_bytes())

    # -------------------------------------------------------------- UI layer
    win = mw.MainWindow()
    win._persist_snapping = False
    win.open_file(source)
    assert win.session.read_only and win.canvas.read_only
    assert "[只读]" in win.windowTitle() and "只读" in win.lblMode.text()
    assert "会话模式" in win.ui.reportBrowser.toPlainText()
    n = len(win.doc.entities)
    assert win.canvas.set_mode("draw_line") is False
    assert win.canvas.mode == "select"
    win.doc.entities[0].selected = True
    win.canvas.delete_selected()
    win.canvas.undo()
    assert len(win.doc.entities) == n, "read-only gate allowed a mutation"

    calls: list = []
    real_write = win.kernel.write
    win.kernel.write = lambda *a, **k: calls.append(a) or real_write(*a, **k)
    msg_exec, msg_clicked = QMessageBox.exec, QMessageBox.clickedButton
    QMessageBox.exec = lambda self: 0
    QMessageBox.clickedButton = lambda self: None
    try:
        win.save()                 # must be refused by the read-only gate
        win.export_dxf()           # rebuild export must be refused too
    finally:
        QMessageBox.exec, QMessageBox.clickedButton = msg_exec, msg_clicked
    assert not calls, "read-only session reached the DWG write pipeline"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    ui_copy = WORK / "readonly_ui_copy.dwg"
    get_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(ui_copy), ""))
    try:
        win.save_as()              # read-only Save As == lossless byte copy
    finally:
        QFileDialog.getSaveFileName = get_save
    assert ui_copy.exists()
    assert hashlib.sha256(ui_copy.read_bytes()).hexdigest() == before
    assert not calls

    dlg = mw.EditModeDialog(win.session, win)
    text = dlg.ui.riskText.toPlainText()
    assert all(word in text for word in ("源文件", "R2004", "降级"))
    dlg.close()
    dlg_exec = mw.EditModeDialog.exec
    mw.EditModeDialog.exec = lambda self: QDialog.Rejected
    try:
        assert win._confirm_enter_edit() is False
    finally:
        mw.EditModeDialog.exec = dlg_exec
    assert win.session.read_only, "cancelling the risk dialog must stay read-only"

    lm = mw.LayerManagerDialog(win.doc, "0", win.canvas.undo_stack, win,
                               read_only=True)
    assert lm.read_only and not lm.ui.btnNewLayer.isEnabled()
    lm.close()

    mw.EditModeDialog.exec = lambda self: QDialog.Accepted
    try:
        assert win._confirm_enter_edit() is True
    finally:
        mw.EditModeDialog.exec = dlg_exec
    assert not win.session.read_only and win.session.degraded
    assert not win.canvas.read_only
    assert "编辑模式" in win.ui.reportBrowser.toPlainText()
    assert "[编辑" in win.windowTitle()
    assert win.canvas.set_mode("draw_line") is True
    assert win._verify_source_unchanged() is True
    assert "源文件保护" in win._source_protect_reason(), \
        "the original source must stay protected even after degradation"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before

    # A real high-version drawing must produce a concrete, quantified risk list.
    real = Path(os.environ.get("ILOVECAD_REAL_DRAWING", ""))
    if not real.is_file():
        candidates = sorted(p for p in (ROOT / "tests").glob("*.dwg")
                            if p.is_file())
        real = candidates[0] if candidates else Path("")
    realworld_risks = False
    if real.is_file():
        rs = kernel.read_session(real)
        risks = "\n".join(rs.risk_lines())
        assert rs.read_only and rs.import_dxf
        assert rs.source_sha256 == hashlib.sha256(real.read_bytes()).hexdigest()
        assert "SPLINE" in risks and "跳过" in risks, \
            "risk list must quantify approximated and skipped objects"
        realworld_risks = True

    win.kernel.write = real_write
    win.close()
    checks = {
        "default_readonly": True,
        "external_change_detected": True,
        "realworld_risks": realworld_risks,
        "canvas_hard_gate": True,
        "lossless_copy_sha256": before[:16],
        "risk_dialog": True,
        "cancel_keeps_readonly": True,
        "confirmed_degradation": True,
        "source_unchanged": True,
    }
    result["readonly"] = checks
    return checks


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    # Keep both theme and snap preferences inside the test workspace.
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(WORK / "settings"))
    result: dict = {}
    step("构建内核 / 文档")
    kernel = LibreDwgKernel()
    doc = make_document()

    step("测量夹角计算校验")
    probe = DwgDocument()
    a90 = measure.angle_three_points(probe, (0.0, 0.0), (10.0, 0.0), (0.0, 10.0))
    a45 = measure.angle_three_points(probe, (0.0, 0.0), (10.0, 0.0), (10.0, 10.0))
    assert a90 and abs(a90["value"] - 90.0) < 1e-9, "three-point angle failed"
    assert a45 and abs(a45["value"] - 45.0) < 1e-9, "45 degree angle failed"
    assert measure.angle_three_points(probe, (0, 0), (0, 0), (5, 5)) is None
    l1 = Entity(1, "LINE", "0", data={"a": (0.0, 0.0), "b": (20.0, 0.0)})
    l2 = Entity(2, "LINE", "0", data={"a": (0.0, 0.0), "b": (0.0, 20.0)})
    b90 = measure.angle_between_lines(probe, l1, l2)
    assert b90 and abs(b90["value"] - 90.0) < 1e-9, "two-line angle failed"
    result["angle_checks"] = {
        "three_point_90": a90["value"], "three_point_45": a45["value"],
        "two_line_90": b90["value"],
        "annotation_strokes": len(a90["paths"]),
    }

    step("对象捕捉类型 / 优先级 / 细分折线隔离校验")
    result["snap_checks"] = snap_checks()

    step("生成 DXF 模型")
    dxf = WORK / "selftest_model.dxf"
    dxf.write_bytes(write_dxf(doc))
    result["dxf_bytes"] = dxf.stat().st_size

    step("写入 R2004 DWG")
    dwg = WORK / "selftest_r2004.dwg"
    write_report = kernel.write(doc, dwg)
    result["dwg_header"] = dwg.read_bytes()[:6].decode("ascii")
    result["dwg_bytes"] = dwg.stat().st_size
    result["write_steps"] = write_report.steps
    assert result["dwg_header"] == TARGET_VERSION, "write gate failed"

    step("回读 DWG")
    reread, read_report = kernel.read(dwg)
    result["reread_entities"] = len(reread.entities)
    result["reread_kinds"] = dict(reread.entity_count_by_kind())
    result["read_steps"] = read_report.steps

    step("构建显示列表")
    dl = build(reread)
    result["display_list_paths"] = len(dl.paths)
    result["display_list_texts"] = len(dl.texts)
    result["bounds"] = list(dl.bounds) if dl.bounds else None

    step("导出 PDF")
    pdf = WORK / "selftest.pdf"
    pdf_info = PdfRenderer.export(str(pdf), reread, dl, PdfOptions(paper="A3"),
                                  view_bounds=None)
    result["pdf_header"] = pdf.read_bytes()[:5].decode("ascii")
    result["pdf_bytes"] = pdf.stat().st_size
    result["pdf_info"] = pdf_info

    step("创建 Qt 应用与主窗口")
    app = QApplication.instance() or QApplication([sys.argv[0]])
    win = MainWindow()
    win._persist_snapping = False
    win.resize(1440, 900)
    step("主窗口打开 DWG")
    win.open_file(dwg)
    app.processEvents()

    step("主题默认值与夹角标注")
    assert win.theme == "dark" and win.canvas.dark, "dark theme must be default"
    angle = measure.angle_three_points(
        win.doc, (0.0, 0.0), (120.0, 0.0), (120.0, 80.0))
    win._on_measurement(angle)
    win.canvas.refresh()
    app.processEvents()
    result["theme_default"] = win.theme
    result["theme_canvas_dark"] = win.canvas.dark
    result["on_canvas_angle"] = round(angle["value"], 6)

    step("设置对话框与本机捕捉配置往返")
    cfg = snap.SnapConfig(
        enabled=False, radius_px=18.0,
        modes={k: k not in ("quadrant", "nearest") for k in snap.SNAP_KINDS})
    save_snap_config(cfg)
    loaded = load_snap_config()
    assert loaded.enabled is False and loaded.radius_px == 18.0
    assert loaded.modes["nearest"] is False and loaded.modes["center"] is True
    save_snap_config(snap.SnapConfig())  # leave the isolated settings file clean
    dlg = SettingsDialog(win.theme, loaded, win)
    dlg.show_snap_page()
    assert dlg.ui.tabSettings.currentWidget() is dlg.ui.tabSnapping
    dlg.ui.spinSnapRadius.setValue(20)
    dlg.ui.checkSnapEndpoint.setChecked(False)
    dialog_cfg = dlg.snap_config()
    assert dialog_cfg.radius_px == 20.0
    assert dialog_cfg.modes["endpoint"] is False and dialog_cfg.enabled is False
    assert dlg.theme() == "dark"
    dlg.close()
    win.canvas.set_snap_config(snap.SnapConfig())
    assert win.lblSnap.text() == "捕捉 开"
    result["settings"] = {
        "tabs": dlg.ui.tabSettings.count(),
        "snap_kinds": len(snap.SNAP_KINDS),
        "restored_radius_px": win.canvas.snap_config.radius_px,
    }

    shot = SHOTS / "selftest_main_window.png"
    step("离屏截图（默认深色）")
    win.grab().save(str(shot))
    result["screenshot"] = str(shot)
    result["screenshot_bytes"] = shot.stat().st_size

    step("离屏截图（浅色主题）")
    win.set_theme("light", persist=False)
    app.processEvents()
    light_shot = SHOTS / "selftest_light_theme.png"
    win.grab().save(str(light_shot))
    result["light_screenshot_bytes"] = light_shot.stat().st_size
    result["light_canvas_dark"] = win.canvas.dark
    win.set_theme("dark", persist=False)  # never touch the user's saved choice

    step("测量结果交互 / 高亮 / 定位 / CSV 导出")
    dist = measure.distance(win.doc, (0.0, 0.0), (120.0, 80.0))
    win._on_measurement(dist)
    item = win.ui.measureList.item(win.ui.measureList.count() - 1)
    win._highlight_measurement(item)
    assert win.canvas._highlight_index == len(win.doc.measurements) - 1
    win._locate_measurement(item)
    assert win.canvas.view.scale > 0
    csv_path = WORK / "measurements.csv"
    import ilovecad.ui.main_window as mw
    original_dialog = mw.QFileDialog.getSaveFileName
    mw.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(csv_path), ""))
    try:
        win._export_measurements_csv()
    finally:
        mw.QFileDialog.getSaveFileName = original_dialog
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "距离" in csv_text and "夹角" in csv_text, "CSV export content failed"
    win._delete_measurement(item)
    assert len(win.doc.measurements) == 1
    win._show_command_help()
    assert "OSNAP" in win.ui.reportBrowser.toPlainText()
    result["measurements"] = {
        "csv_bytes": csv_path.stat().st_size,
        "remaining": len(win.doc.measurements),
        "help_chars": len(win.ui.reportBrowser.toPlainText()),
    }

    step("批量转换（DWG + DXF，原文件不改写）")
    before = hashlib.sha256(dwg.read_bytes()).hexdigest()
    batch_out = WORK / "batch"
    batch = BatchConvertDialog(kernel, files=[dwg], output_dir=batch_out)
    r_dwg = batch.run_conversion([dwg], batch_out, "dwg")
    r_dxf = batch.run_conversion([dwg], batch_out, "dxf")
    assert r_dwg["ok"] == 1 and r_dxf["ok"] == 1, "batch conversion failed"
    t_dwg, t_dxf = Path(r_dwg["targets"][0]), Path(r_dxf["targets"][0])
    assert t_dwg.read_bytes()[:6].decode("ascii") == TARGET_VERSION
    assert b"AC1018" in t_dxf.read_bytes()[:4096]
    assert unique_target(batch_out, dwg, "dwg").name != t_dwg.name
    assert hashlib.sha256(dwg.read_bytes()).hexdigest() == before
    batch.close()
    result["batch"] = {
        "dwg": t_dwg.name, "dxf": t_dxf.name,
        "dwg_bytes": t_dwg.stat().st_size, "dxf_bytes": t_dxf.stat().st_size,
        "source_unchanged": True,
    }

    result["window_title"] = win.windowTitle()
    result["ui_widgets"] = {
        "ribbon_tabs": win.ui.ribbonTabs.count(),
        "layer_items": win.ui.layerTree.topLevelItemCount(),
        "measure_items": win.ui.measureList.count(),
        "measure_tab_buttons": win.ui.layoutMeasure.count() - 1,
        "output_tab_buttons": win.ui.layoutOutput.count() - 1,
    }
    win.close()

    step("高版本降版本样例")
    high = Path(os.environ.get("ILOVECAD_HIGH_VERSION_SAMPLE", ""))
    # Path("") resolves to ".", which exists() reports as True but is not a file.
    if high.is_file():
        hv_doc, hv_report = kernel.read(high)
        result["high_version"] = {
            "file": high.name,
            "source": hv_report.source_version,
            "effective": hv_report.effective_version,
            "downgraded": hv_report.downgraded,
            "entities": len(hv_doc.entities),
            "steps": hv_report.steps,
        }

    step("红队查漏补缺回归 + 真实图纸")
    redteam_checks(kernel, result)

    step("只读 / 编辑分层回归")
    readonly_checks(kernel, result)

    step("根入口 main.py 与命令行直接打开图纸")
    entry = ROOT / "main.py"
    assert entry.is_file(), "根目录 main.py 缺失"
    import ilovecad.app as app_module

    created: list[MainWindow] = []
    real_cls, real_exec = app_module.MainWindow, QApplication.exec

    class _Probe(real_cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created.append(self)

    sample = WORK / "selftest_r2004.dwg"
    app_module.MainWindow = _Probe
    QApplication.exec = lambda self: 0        # 不进入交互式事件循环
    try:
        app_module.main([str(sample)])
    finally:
        app_module.MainWindow = real_cls
        QApplication.exec = real_exec
    assert len(created) == 1, "main() 未创建主窗口"
    win = created[0]
    assert Path(win.current_path or "") == sample, "命令行图纸未被打开"
    assert win.doc.entities, "命令行图纸解析为空"
    result["entrypoint"] = {"file": "main.py", "argv_drawing": str(sample),
                            "entities": len(win.doc.entities)}
    win.close()

    step("SPDX 头覆盖检查")
    sources = [p for p in list((ROOT / "src").rglob("*.py"))
               + list((ROOT / "src").rglob("*.ui"))
               + list((ROOT / "scripts").glob("*.py"))
               + [entry]
               if "generated" not in p.parts and "__pycache__" not in p.parts]
    missing = [str(p.relative_to(ROOT)) for p in sources
               if "SPDX-License-Identifier: GPL-3.0-or-later"
               not in p.read_text(encoding="utf-8")[:400]]
    assert not missing, f"SPDX header missing: {missing}"
    result["spdx"] = {"files": len(sources), "missing": missing}

    step("完成，输出汇总")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
