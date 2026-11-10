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
    assert sd.ui.tabSettings.count() == 4
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


def render_checks(kernel: LibreDwgKernel, result: dict) -> dict:
    """Display pipeline regressions: MTEXT, HATCH, NURBS, dominant fit, PDF."""
    from ilovecad.kernel.dxf_codec import parse_dxf
    from ilovecad.model import geometry as g
    from ilovecad.render.displaylist import build
    from ilovecad.render.pdf import PdfOptions, PdfRenderer
    from ilovecad.ui.main_window import PdfExportDialog

    checks: dict = {}
    dlg = PdfExportDialog()
    assert dlg.ui.comboScope.count() == 3
    assert dlg.options().scope == "全图"
    dlg.ui.comboScope.setCurrentIndex(2)
    assert dlg.options().scope.startswith("完整范围")
    dlg.close()
    checks["pdf_scope"] = ["全图", "当前视图", "完整范围"]

    step("渲染回归：MTEXT 格式码 / 多行 / 纯格式碎片")
    assert g.visible_text_lines(
        r"{\fSimHei|b0|i0|c134|p49;DN15气动阀}") == ["DN15气动阀"]
    assert g.visible_text_lines(r"|b0|i0|c1|p0;\o\l") == []
    assert g.visible_text_lines(r"\A1;3000") == ["3000"]
    note = (r"{\C4; \H1.5x; \fKaiTi|b0|i0|c134|p49;"
            r"\H0.8333x; 技术要求：\P1 辊筒面高于机架面10mm"
            r"\P2 辊筒采用直径76*4}")
    assert g.visible_text_lines(note) == [
        "技术要求：", "1 辊筒面高于机架面10mm", "2 辊筒采用直径76*4"]
    checks["mtext_cleanup"] = True

    step("渲染回归：STYLE 字型 / 内联格式码 / 字体映射 / 宽度与对齐")
    from ilovecad.fonts import font_families, normalize_font_name
    assert normalize_font_name(r"C:\FONTS\SimHei.ttf") == "simhei"
    fam = font_families("isocp.shx", "hztxt.shx", "SimHei")
    assert fam[0] in ("SimHei", "黑体") and "SimSun" in fam, fam
    assert font_families("KaiTi")[0] in ("KaiTi", "楷体")
    assert font_families("HYSWLongFangSong")[0] in ("FangSong", "仿宋")
    assert font_families("", "", "", "PDF 汉仪长仿宋体")[0] in ("FangSong", "仿宋")
    fmt = g.parse_mtext_format(
        r"{\fSimHei|b1|i1|c134|p49;\H0.8333x;\W1.4;\Q12;\C3;文字}")
    assert (fmt["inline_font"] == "SimHei" and fmt["bold"] and fmt["italic"]
            and abs(fmt["height_scale"] - 0.8333) < 1e-3
            and abs(fmt["width_scale"] - 1.4) < 1e-3
            and fmt["oblique"] == 12 and fmt["color_index"] == 3), fmt
    # STYLE table + zero-height TEXT must fall back to the style's last height,
    # and TEXT group 11/21 must win as the alignment point.
    style_pairs = [("0", "SECTION"), ("2", "TABLES"),
                   ("0", "TABLE"), ("2", "STYLE"),
                   ("0", "STYLE"), ("2", "HZ"), ("3", "isocp.shx"),
                   ("4", "hztxt.shx"), ("40", "0.0"), ("41", "0.8"),
                   ("42", "7.5"), ("50", "0.0"),
                   ("0", "ENDTAB"), ("0", "ENDSEC"),
                   ("0", "SECTION"), ("2", "ENTITIES"),
                   ("0", "TEXT"), ("8", "0"), ("10", "1.0"), ("20", "2.0"),
                   ("11", "9.0"), ("21", "8.0"), ("40", "0.0"),
                   ("72", "1"), ("73", "0"), ("7", "HZ"), ("1", "样式文字"),
                   ("0", "ENDSEC"), ("0", "EOF")]
    styled, _ = parse_dxf(
        "".join(f"{c}\n{v}\n" for c, v in style_pairs).encode("utf-8"))
    t = styled.entities[0]
    assert t.data["height"] == 7.5, "zero-height TEXT lost its STYLE fallback"
    assert t.data["p"] == (9.0, 8.0), "TEXT alignment point was ignored"
    assert abs(t.data["width_factor"] - 0.8) < 1e-9
    assert t.data["font"] == "isocp.shx" and t.data["bigfont"] == "hztxt.shx"
    sdl = build(styled)
    assert abs(sdl.texts[0].width_scale - 0.8) < 1e-9
    assert "SimSun" in sdl.texts[0].families
    back_style, _ = parse_dxf(write_dxf(styled))
    assert "HZ" in back_style.styles
    assert back_style.styles["HZ"]["font"] == "isocp.shx"
    assert back_style.styles["HZ"]["bigfont"] == "hztxt.shx"
    checks["style_font_mapping"] = {"styles": len(styled.styles),
                                    "families": sdl.texts[0].families[:3],
                                    "roundtrip_styles": len(back_style.styles)}

    doc = DwgDocument()
    for i in range(60):
        doc.add(Entity(0, "LINE", "0", data={"a": (float(i), 0.0),
                                             "b": (float(i), 100.0)}))
    doc.add(Entity(0, "TEXT", "0", data={
        "p": (0.0, 120.0), "text": note, "height": 10.0, "rotation": 0.0,
        "mtext": True, "align": 1, "width_factor": 1.0, "column_width": 0.0}))
    doc.add(Entity(0, "TEXT", "0", data={
        "p": (0.0, 140.0), "text": r"|b0|i0|c1|p0;\o\l", "height": 10.0}))
    doc.add(Entity(0, "HATCH", "0", data={"paths": [
        [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0)],
        [(3.0, 3.0, 0.0), (3.0, 7.0, 0.0), (7.0, 7.0, 0.0), (7.0, 3.0, 0.0)]]}))
    dl = build(doc)
    texts = [t for t in dl.texts if not t.annotation]
    assert len(texts) == 1, "pure formatting debris was rendered as text"
    assert texts[0].lines == ["技术要求：", "1 辊筒面高于机架面10mm",
                              "2 辊筒采用直径76*4"]
    hatch = [p for p in dl.paths if p.kind == "HATCH"]
    assert hatch and not any(p.filled for p in hatch), \
        "HATCH was painted as a solid blob again"
    assert sum(1 for p in hatch if not p.closed) > 0, \
        "hatch pattern strokes were not generated"
    checks["hatch_outline"] = len(hatch)

    step("渲染回归：SPLINE 真实 NURBS 求值与节点回退")
    ctrl = [(0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (4.0, 0.0), (5.0, 1.0)]
    knots = [0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 2.0, 2.0]
    curve = g.nurbs_points(ctrl, knots, 3)
    assert len(curve) > len(ctrl)
    assert abs(curve[0][0] - ctrl[0][0]) < 1e-6
    assert abs(curve[-1][0] - ctrl[-1][0]) < 1e-6
    assert len(g.nurbs_points(ctrl, [], 3)) > len(ctrl), \
        "missing knot vector did not fall back to a clamped uniform spline"
    pairs = [("0", "SECTION"), ("2", "ENTITIES"), ("0", "SPLINE"),
             ("8", "0"), ("70", "8"), ("71", "3"), ("72", "9"),
             ("73", "5"), ("74", "0")]
    pairs += [("40", str(v)) for v in knots]
    for x, y in ctrl:
        pairs += [("10", str(x)), ("20", str(y))]
    pairs += [("0", "ENDSEC"), ("0", "EOF")]
    parsed, _ = parse_dxf(
        "".join(f"{c}\n{v}\n" for c, v in pairs).encode("utf-8"))
    sp = parsed.entities[0]
    assert sp.kind == "SPLINE"
    assert len(sp.data["pts"]) > len(sp.data["control"])
    assert sp.data["degree"] == 3 and len(sp.data["knots"]) == 9
    checks["spline_nurbs"] = {"control": len(sp.data["control"]),
                              "evaluated": len(sp.data["pts"])}

    step("渲染回归：字高层级 / 原样 1:1 / 显示倍率 / 源文件字高不变")
    big = DwgDocument()
    for i in range(60):
        big.add(Entity(0, "LINE", "0", data={"a": (float(i), 0.0),
                                             "b": (float(i), 100.0)}))
    big.add(Entity(0, "TEXT", "0", data={"p": (0.0, 0.0), "text": "大标题",
                                         "height": 10000.0, "mtext": True}))
    big.add(Entity(0, "TEXT", "0", data={"p": (0.0, -50.0), "text": "小标注",
                                         "height": 2000.0, "mtext": True}))
    cap = big.text_height_cap()
    assert cap and cap < 10000
    big.text_height_mode = "auto"      # opt-in compression is no longer default
    auto = build(big)
    heights = [t.height for t in auto.texts]
    assert len(heights) == 2 and heights[0] > heights[1] > cap, \
        "adaptive mode flattened the source type hierarchy"
    assert auto.texts[0].source_height == 10000.0
    assert big.entities[-2].data["height"] == 10000.0
    big.text_height_mode = "exact"
    exact = build(big)
    assert exact.texts[0].height == 10000.0 and exact.texts[1].height == 2000.0
    big.text_scale = 0.5
    assert build(big).texts[0].height == 5000.0
    big.text_scale = 1.0
    big.text_height_mode = "auto"
    checks["text_height_cap"] = round(cap, 3)
    checks["text_hierarchy"] = [round(h, 1) for h in heights]

    step("渲染回归：旧式 MTEXT 分组码按 DWG 原生字段修复")
    from ilovecad.kernel.native_text import (apply_native_text,
                                             legacy_mtext_dialect)

    def mtext_dxf(pairs):
        body = [("0", "SECTION"), ("2", "ENTITIES")] + pairs + [
            ("0", "ENDSEC"), ("0", "EOF")]
        return "".join(f"{c}\n{v}\n" for c, v in body).encode("utf-8")

    legacy = mtext_dxf([
        ("0", "MTEXT"), ("5", "A1"), ("8", "0"), ("10", "100"),
        ("20", "50"), ("40", "3357.4"), ("41", "0.0"),
        ("44", "3357.4"), ("71", "2"), ("72", "1"),
        ("1", "AWLF-004")])
    standard = mtext_dxf([
        ("0", "MTEXT"), ("5", "A1"), ("8", "0"), ("10", "100"),
        ("20", "50"), ("40", "89.43"), ("41", "3357.4"),
        ("44", "1.0"), ("71", "1"), ("1", "AWLF-004")])
    assert legacy_mtext_dialect(legacy), "failed to spot legacy MTEXT codes"
    assert not legacy_mtext_dialect(standard), \
        "standard MTEXT codes were mistaken for the legacy dialect"
    bad, _ = parse_dxf(legacy)
    assert bad.entities[0].data["height"] == 3357.4
    assert bad.entities[0].data["align"] == 1, \
        "legacy group 72 attachment was not recovered"
    good, _ = parse_dxf(standard)
    assert abs(good.entities[0].data["height"] - 89.43) < 1e-6
    assert good.entities[0].data["column_chars"] > 20, \
        "MTEXT group 41 was not interpreted as a drawing-unit rectangle width"
    fixed = apply_native_text(bad, {0xA1: {
        "kind": "MTEXT", "height": 89.43, "align": 1,
        "rect_width": 3357.4, "line_spacing": 1.0,
        "ins_pt": [100.0, 50.0, 0.0], "text": "AWLF-004"}})
    assert fixed == 1
    assert abs(bad.entities[0].data["height"] - 89.43) < 1e-6
    assert bad.entities[0].data["align"] == 1

    framed = DwgDocument()
    corners = [(0.0, 0.0), (10000.0, 0.0), (10000.0, 7000.0), (0.0, 7000.0)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        framed.add(Entity(0, "LINE", "0", data={"a": a, "b": b}))
    for i in range(60):
        framed.add(Entity(0, "LINE", "0",
                          data={"a": (i * 100.0, 1000.0),
                                "b": (i * 100.0, 2000.0)}))
    framed.add(Entity(0, "LINE", "0",
                      data={"a": (20000.0, 20000.0),
                            "b": (20100.0, 20000.0)}))
    assert framed.sheet_frame_bounds() == (0.0, 0.0, 10000.0, 7000.0), \
        "sheet border rectangle was not detected for zoom extents"

    step("真实图纸：主体图幅视图 / 无格式码 / 无实心填充 / PDF 完整范围")
    real = Path(os.environ.get("ILOVECAD_REAL_DRAWING", ""))
    if not real.is_file():
        candidates = sorted(p for p in (ROOT / "tests").glob("*.dwg")
                            if p.is_file())
        real = candidates[0] if candidates else Path("")
    if real.is_file():
        s = kernel.read_session(real)
        d = s.document
        full = d.bounds()
        fit_bounds, ignored = d.robust_bounds()
        assert fit_bounds and ignored > 0
        assert (fit_bounds[2] - fit_bounds[0]) < (full[2] - full[0]) * 0.6, \
            "zoom extents is still dominated by remote sheets"
        rdl = build(d)
        assert not any(p.filled for p in rdl.paths if p.kind == "HATCH")
        all_lines = [ln for t in rdl.texts for ln in t.lines]
        assert all_lines and all("|b0" not in ln and "{\\" not in ln
                                 and "\\o\\l" not in ln for ln in all_lines)
        assert max(len(ln) for ln in all_lines) < 200, \
            "a multi-line note was flattened into one endless line"
        splines = [e for e in d.entities if e.kind == "SPLINE"]
        assert splines and all(len(e.data["pts"]) >= len(e.data["control"])
                               for e in splines)
        assert len(d.styles) >= 5, "STYLE table was not parsed"
        families = {tuple(t.families[:1]) for t in rdl.texts}
        heights = {round(t.height, 1) for t in rdl.texts}
        assert len(families) >= 3, "all labels fell back to one font family"
        assert len(heights) >= 8, "all labels were flattened to one size"
        # dwgread -O DXF used to write the MTEXT reference-rectangle WIDTH
        # into group 40.  A 89-unit title-block label became 3357 units.
        # dwg2dxf's standard DXF codes must keep the native text heights.
        assert max(t.source_height for t in rdl.texts) < 500, \
            "legacy MTEXT dialect still inflates text heights"
        title = [e for e in d.entities
                 if "AWLF-004" in str(e.data.get("text", ""))]
        assert title and 80.0 < title[0].data["height"] < 100.0, \
            "title-block MTEXT height was not restored from the DWG"
        assert title[0].data["align"] == 1, \
            "MTEXT attachment point was not restored"
        # TEXT group 73 is dropped by dwg2dxf; the alignment point 11/21 is
        # the cell centre, so labels must be middle-centre, not baseline.
        title_texts = [e for e in d.entities
                       if e.data.get("source_kind") == "TEXT" and
                       44000 < e.data["p"][0] < 48700 and
                       21800 < e.data["p"][1] < 23300]
        assert title_texts and all(e.data["align"] == 5 for e in title_texts), \
            "title-block TEXT vertical justification was not recovered"
        # Grid alignment is display-only and must stay inside the title block.
        from ilovecad.model.titleblock import apply_title_block_alignment
        frame = d.sheet_frame_bounds()
        assert frame
        rx0 = frame[0] + 0.45 * (frame[2] - frame[0])
        ry1 = frame[1] + 0.45 * (frame[3] - frame[1])
        aligned = [e for e in d.entities if e.data.get("display_offset")]
        assert aligned, "title-block grid alignment was not applied"
        assert all(rx0 <= e.data["p"][0] <= frame[2] and
                   frame[1] <= e.data["p"][1] <= ry1 for e in aligned), \
            "grid alignment moved a label outside the title block"
        assert apply_title_block_alignment(d, False) == 0
        assert not any(e.data.get("display_offset") for e in d.entities)
        assert apply_title_block_alignment(d, True) > 0, \
            "title-block alignment cannot be re-enabled"
        assert d.text_height_mode == "exact", \
            "viewer default must show the source font sizes 1:1"
        exact_dl = build(d)
        assert len(exact_dl.texts) == len(rdl.texts)
        assert all(abs(a.height - b.height) < 1e-9
                   for a, b in zip(exact_dl.texts, rdl.texts)), \
            "default display is not the exact 1:1 source size"
        d.text_height_mode = "auto"
        auto_dl = build(d)
        assert any(abs(a.height - b.height) > 1e-6
                   for a, b in zip(auto_dl.texts, exact_dl.texts)), \
            "opt-in adaptive mode no longer compresses pathological heights"
        # Top-attached MTEXT must be drawn BELOW its insertion point, not
        # above it; bottom / baseline TEXT must not be shifted at all.
        top = g.text_baselines(1, 100.0, 85.0, 25.0, 1.0, 1)
        bottom = g.text_baselines(1, 100.0, 85.0, 25.0, 1.0, 8,
                                  baseline=True)
        assert top[0] > 50 and bottom[0] == 0.0, \
            "MTEXT baseline offsets ignore the attachment point"
        frame = d.sheet_frame_bounds()
        assert frame and frame == fit_bounds, \
            "real sheet frame was not detected / used for zoom extents"
        assert "艾力威" in "".join(all_lines), \
            "right-hand title-block column was cropped out of the sheet"
        full_pdf = WORK / "render_full_extent.pdf"
        PdfRenderer.export(str(full_pdf), d, rdl,
                           PdfOptions(paper="A3", scope="完整范围"), None)
        assert full_pdf.read_bytes()[:5] == b"%PDF-"
        checks["realworld_render"] = {
            "full_width": round(full[2] - full[0], 1),
            "fit_width": round(fit_bounds[2] - fit_bounds[0], 1),
            "ignored": ignored, "text_lines": len(all_lines),
            "hatch_paths": sum(1 for p in rdl.paths if p.kind == "HATCH"),
            "styles": len(d.styles), "font_families": len(families),
            "distinct_heights": len(heights),
            "full_extent_pdf_bytes": full_pdf.stat().st_size,
        }
    result["render"] = checks
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


def editmode_checks(kernel: LibreDwgKernel, result: dict) -> dict:
    """Edit-module regressions: lossless Save As, reversible degradation,
    multi-line MTEXT write-back and the title-alignment toggle."""
    from PySide6.QtWidgets import QDialog, QFileDialog

    import ilovecad.ui.main_window as mw
    from ilovecad.kernel.dxf_codec import parse_dxf, write_dxf
    from ilovecad.model.session import EDITABLE, DocumentSession
    from ilovecad.model.titleblock import apply_title_block_alignment

    checks: dict = {}

    # 1. Multi-line MTEXT must not be flattened into a literal "\P".
    doc = DwgDocument()
    doc.add(Entity(0, "TEXT", "0", data={
        "p": (0.0, 0.0), "text": "第一行\n第二行\n第三行",
        "height": 4.0, "mtext": True, "align": 1,
        "line_spacing": 1.0, "width_factor": 1.0}))
    stats: dict = {}
    raw = write_dxf(doc, stats)
    assert stats.get("mtext_lines") == 3, "multi-line MTEXT was not split"
    back, _ = parse_dxf(raw)
    written_lines = [str(e.data.get("text", "")) for e in back.entities
                     if e.kind == "TEXT"]
    assert written_lines == ["第一行", "第二行", "第三行"], \
        "write-back lost the MTEXT line breaks"
    assert not any("\\P" in t for t in written_lines), \
        "literal \\P leaked into the saved TEXT"
    checks["mtext_lines"] = written_lines

    # 2. A degraded session can be restored from its in-memory DXF snapshot.
    session = DocumentSession(
        source_path=None, source_sha256="", source_format="dxf",
        source_version="AC1018", import_dxf=raw, document=doc,
        mode=EDITABLE, degraded=True, edit_started_at="2026-01-01T00:00:00",
        risk_snapshot=["risks"])
    fresh, _ = parse_dxf(raw)
    session.restore_readonly(fresh)
    assert session.read_only and not session.degraded
    assert session.document is fresh and not session.risk_snapshot
    assert session.edit_started_at == ""
    checks["restore_readonly_unit"] = True

    # 3. Real window: enter edit -> restore read-only -> unedited Save As is
    #    a byte-identical copy, not an R2004 rebuild.
    sample = WORK / "selftest_r2004.dwg"
    assert sample.is_file(), "run the R2004 write test first"
    before = hashlib.sha256(sample.read_bytes()).hexdigest()
    win = MainWindow()
    win.open_file(sample)
    original_entities = len(win.doc.entities)
    orig_dialog = mw.EditModeDialog.exec
    orig_get = mw.QFileDialog.getSaveFileName
    mw.EditModeDialog.exec = lambda *a: QDialog.Accepted
    try:
        win._confirm_enter_edit()
        assert not win.session.read_only and win.session.degraded
        win._restore_readonly()
        assert win.session.read_only and not win.session.degraded
        assert len(win.doc.entities) == original_entities
        # Enter edit again but make NO edits: Save As must copy bytes.
        win._confirm_enter_edit()
        assert not win.canvas.undo_stack.can_undo
        target = WORK / "editmode_unedited_saveas.dwg"
        mw.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (str(target), ""))
        try:
            win.save_as()
        finally:
            mw.QFileDialog.getSaveFileName = orig_get
        assert target.is_file() and \
            hashlib.sha256(target.read_bytes()).hexdigest() == before, \
            "unedited Save As rebuilt the drawing instead of copying it"
        assert hashlib.sha256(sample.read_bytes()).hexdigest() == before
        checks["unedited_saveas_lossless"] = True

        # 4. TITLEALIGN toggle and the settings checkbox.
        win._set_title_align(False)
        assert not win.doc.align_title_text
        assert not any(e.data.get("display_offset") for e in win.doc.entities)
        win._set_title_align(True)
        assert win.doc.align_title_text
        sd = mw.SettingsDialog(win.theme, win.canvas.snap_config, win,
                               title_align=True)
        assert sd.ui.checkTitleAlign.isChecked()
        sd.ui.checkTitleAlign.setChecked(False)
        assert not sd.title_align_enabled()
        sd.close()
        checks["title_align_toggle"] = True
    finally:
        mw.EditModeDialog.exec = orig_dialog
        mw.QFileDialog.getSaveFileName = orig_get
        win.close()
    result["editmode"] = checks
    return checks


def optimization_checks(kernel: LibreDwgKernel, result: dict) -> dict:
    """Zoom cache, live transform preview, colour editing and context menu."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import QApplication, QMenu

    from ilovecad.kernel.dxf_codec import parse_dxf
    from ilovecad.preferences import load_draw_color, save_draw_color
    from ilovecad.render.colors import entity_color
    from ilovecad.render.qt_paint import paint_display_list
    from ilovecad.ui.canvas import PREVIEW_MODES
    from ilovecad.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0]])
    win = MainWindow()
    win._persist_snapping = False
    win.resize(1200, 800)
    dense = DwgDocument()
    for i in range(400):
        dense.add(Entity(0, "LINE", "0", data={
            "a": (float(i), 0.0), "b": (float(i), 120.0)}))
    win.doc = dense
    win.canvas.set_document(dense)
    win.show()
    app.processEvents()

    checks: dict = {}

    # ---- static scene cache: a zoom gesture must NOT rebuild 400 strokes ----
    win.canvas.grab()
    win.canvas._begin_interactive()      # first gesture frame renders 1x
    scene = win.canvas._scene
    assert scene is not None, "static scene pixmap was not rendered"
    for _ in range(20):
        win.canvas.view.zoom(1.05)
        win.canvas._begin_interactive()
        win.canvas.grab()
    assert win.canvas._scene is scene, \
        "zoom gesture rebuilt the full scene instead of blitting the cache"
    win.canvas._finish_interactive()
    win.canvas.grab()
    assert win.canvas._scene is not scene, \
        "no full-quality repaint after the zoom gesture settled"
    n_before = len(win.canvas._bounds_cache)
    b1 = win.canvas._entity_bounds(dense.entities[0])
    b2 = win.canvas._entity_bounds(dense.entities[0])
    assert b1 == b2 and len(win.canvas._bounds_cache) == n_before + 1
    checks["zoom_cache"] = {"interactive_frames": 20,
                            "scene_rebuilt": False,
                            "bounds_cache": len(win.canvas._bounds_cache)}
    win.canvas.view.fit(dense.bounds())

    # ---- drawing colour, batch recolor, DXF 420 round trip -----------------
    assert save_draw_color((12, 34, 56)) == (12, 34, 56)
    assert load_draw_color() == (12, 34, 56)
    win._apply_draw_color((12, 34, 56))
    fresh = win.canvas._new("LINE", {"a": (0.0, 0.0), "b": (10.0, 10.0)})
    assert fresh.data["true_color"] == (12, 34, 56)
    assert 1 <= fresh.color <= 255
    assert entity_color(fresh, dense, False) == (12, 34, 56)
    target = dense.entities[0]
    target.selected = True
    assert win.canvas.change_selected_color((200, 30, 40)) == 1
    assert target.data["true_color"] == (200, 30, 40)
    assert entity_color(target, dense, False) == (200, 30, 40)
    raw = write_dxf(dense)
    expected = (200 << 16) | (30 << 8) | 40
    assert (f"\n420\n{expected}\n").encode("ascii") in raw, \
        "true colour was not written as DXF group 420"
    back, _ = parse_dxf(raw)
    assert any(e.data.get("true_color") == (200, 30, 40)
               for e in back.entities), "DXF 420 was lost on re-import"
    win.canvas.undo()
    # Undo restores a snapshot, so document entities are NEW objects; check
    # the live list instead of the detached pre-undo reference.
    assert dense.entities[0].data.get("true_color") != (200, 30, 40), \
        "colour change was not undoable"

    # A real R2004 write / read round trip must keep the exact RGB too.
    from ilovecad.render.colors import nearest_aci
    colour_doc = DwgDocument()
    ce = Entity(0, "LINE", "0", data={"a": (0.0, 0.0), "b": (10.0, 0.0)})
    ce.data["true_color"] = (200, 30, 40)
    ce.color = nearest_aci((200, 30, 40))
    colour_doc.add(ce)
    colour_dwg = WORK / "opt_color_r2004.dwg"
    kernel.write(colour_doc, colour_dwg)
    colour_back, _ = kernel.read(colour_dwg)
    assert colour_dwg.read_bytes()[:6] == TARGET_VERSION.encode("ascii")
    assert any(e.data.get("true_color") == (200, 30, 40)
               for e in colour_back.entities), "R2004 lost the true colour"
    save_draw_color(None)
    checks["colour"] = {"draw_rgb": [12, 34, 56],
                        "batch_rgb": [200, 30, 40],
                        "dxf_420": expected, "undoable": True,
                        "r2004_true_colour": True,
                        "by_layer_default": load_draw_color() is None}

    # ---- live transform preview + dimmed source ----------------------------
    dense.entities[0].selected = True
    assert win.canvas.set_mode("move")
    win.canvas._after_selection()
    assert "move" in PREVIEW_MODES
    win.canvas.points = [(0.0, 0.0)]
    win.canvas.cursor_model = (10.0, 5.0)
    assert win.canvas._fade_selection(), \
        "source strokes were not dimmed after picking the base point"
    fn = win.canvas._preview_fn()
    assert fn is not None and fn((1.0, 2.0)) == (11.0, 7.0)
    assert win.canvas._preview_source, "preview geometry was not snapshotted"
    dl = build(dense)
    pix = QPixmap(400, 300)
    qp = QPainter(pix)
    paint_display_list(qp, dl, win.canvas.view, True, fade_selection=True)
    qp.end()
    win.canvas.grab()
    assert win.canvas.set_mode("select")
    assert not win.canvas._fade_selection()
    checks["preview"] = {"modes": sorted(PREVIEW_MODES),
                         "delta": [10.0, 5.0],
                         "strokes": len(win.canvas._preview_source) or 1}

    # ---- right-click menu: every editing command is reachable --------------
    menu = win._show_canvas_context_menu(QPoint(5, 5), True, execute=False)
    labels = [a.text() for a in menu.actions()]
    submenus = {m.title(): [a.text() for a in m.actions()]
                for m in menu.findChildren(QMenu) if m.actions()}
    all_labels = labels + [t for group in submenus.values() for t in group]
    required = ("移动（实时预览）", "复制（实时预览）", "旋转（实时预览）",
                "镜像（实时预览）", "缩放（比例）", "删除", "线条改色…",
                "直线", "多段线", "矩形", "圆", "圆弧", "文字",
                "绘图颜色…", "撤销 (Ctrl+Z)", "重做 (Ctrl+Y)",
                "适应全图 (Z)", "图层管理…")
    missing = [name for name in required if name not in all_labels]
    assert not missing, f"context menu is missing: {missing}"
    checks["context_menu"] = {"top_items": len(labels),
                              "submenus": {k: len(v) for k, v in submenus.items()},
                              "required": len(required)}

    win.close()
    result["optimization"] = checks
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

    step("渲染管线回归（MTEXT / HATCH / NURBS / 主体图幅 / PDF）")
    render_checks(kernel, result)

    step("只读 / 编辑分层回归")
    readonly_checks(kernel, result)

    step("编辑模块改造回归（恢复只读 / 无损另存 / 多行文字 / 格线开关）")
    editmode_checks(kernel, result)

    step("优化回归（缩放缓存 / 颜色 / 实时预览 / 右键菜单）")
    optimization_checks(kernel, result)

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
