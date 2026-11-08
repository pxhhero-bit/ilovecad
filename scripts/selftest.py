#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless end-to-end self test with an offscreen Qt window screenshot."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ilovecad.kernel.core import LibreDwgKernel  # noqa: E402
from ilovecad.kernel.dxf_codec import write_dxf  # noqa: E402
from ilovecad.model.document import DwgDocument  # noqa: E402
from ilovecad.model.entities import Entity  # noqa: E402
from ilovecad.render.displaylist import build  # noqa: E402
from ilovecad.render.pdf import PdfOptions, PdfRenderer  # noqa: E402
from ilovecad.tools import measure  # noqa: E402
from ilovecad.ui.main_window import MainWindow  # noqa: E402
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


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
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

    result["window_title"] = win.windowTitle()
    result["ui_widgets"] = {
        "ribbon_tabs": win.ui.ribbonTabs.count(),
        "layer_items": win.ui.layerTree.topLevelItemCount(),
        "measure_items": win.ui.measureList.count(),
        "measure_tab_buttons": win.ui.layoutMeasure.count() - 1,
    }
    win.close()

    step("高版本降版本样例")
    high = Path(os.environ.get("ILOVECAD_HIGH_VERSION_SAMPLE", ""))
    if high.exists():
        hv_doc, hv_report = kernel.read(high)
        result["high_version"] = {
            "file": high.name,
            "source": hv_report.source_version,
            "effective": hv_report.effective_version,
            "downgraded": hv_report.downgraded,
            "entities": len(hv_doc.entities),
            "steps": hv_report.steps,
        }

    step("完成，输出汇总")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
