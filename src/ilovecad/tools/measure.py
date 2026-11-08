# SPDX-License-Identifier: GPL-3.0-or-later
"""2D measurement commands and unit-aware formatting."""
from __future__ import annotations

import math

from ..model import geometry as g
from ..model.document import DwgDocument
from ..model.entities import Entity

Point = tuple[float, float]


def _record(doc: DwgDocument, kind: str, value: float, label: str,
            points: list[Point], closed: bool = False, extra: str = "") -> dict:
    m = {"kind": kind, "value": value, "label": label,
         "points": [tuple(p) for p in points], "closed": closed, "extra": extra}
    doc.measurements.append(m)
    return m


def distance(doc: DwgDocument, p1: Point, p2: Point) -> dict:
    d = g.dist(p1, p2)
    dx, dy = abs(p2[0] - p1[0]), abs(p2[1] - p1[1])
    ang = g.angle_deg(p1, p2)
    extra = (f"ΔX={dx:.{doc.precision}f}  ΔY={dy:.{doc.precision}f}  "
             f"角度={ang:.2f}°")
    return _record(doc, "距离", d,
                   g.format_length(d, doc.units, doc.precision),
                   [p1, p2], extra=extra)


def chain(doc: DwgDocument, points: list[Point]) -> dict:
    total = g.polyline_length(points)
    segments = [g.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    extra = "分段：" + " / ".join(f"{s:.{doc.precision}f}" for s in segments)
    return _record(doc, "累计长度", total,
                   g.format_length(total, doc.units, doc.precision),
                   points, extra=extra)


def area(doc: DwgDocument, points: list[Point]) -> dict:
    a = g.polygon_area(points)
    per = g.polyline_length(points, closed=True)
    extra = f"周长：{g.format_length(per, doc.units, doc.precision)}"
    return _record(doc, "面积", a,
                   g.format_area(a, doc.units, doc.precision),
                   points, closed=True, extra=extra)


def radius(doc: DwgDocument, ent: Entity) -> dict | None:
    if ent.kind not in ("CIRCLE", "ARC"):
        return None
    d = ent.data
    r = float(d["r"])
    if ent.kind == "ARC":
        start, end = float(d["start"]), float(d["end"])
        while end < start:
            end += 360.0
        sweep = min(end - start, 360.0)
        length = 2 * math.pi * r * sweep / 360.0
        extra = f"直径={2 * r:.{doc.precision}f}  弧长={length:.{doc.precision}f}  圆心角={sweep:.2f}°"
        label = f"R {r:.{doc.precision}f} / 弧长 {g.format_length(length, doc.units, doc.precision)}"
    else:
        length = 2 * math.pi * r
        extra = (f"直径={2 * r:.{doc.precision}f}  "
                 f"周长={g.format_length(length, doc.units, doc.precision)}  "
                 f"面积={g.format_area(math.pi * r * r, doc.units, doc.precision)}")
        label = f"R {r:.{doc.precision}f} / ⌀{2 * r:.{doc.precision}f}"
    c = tuple(d["c"])
    return _record(doc, "半径", r, label, [c, (c[0] + r, c[1])], extra=extra)


def entity_length(doc: DwgDocument, ent: Entity) -> dict | None:
    total = sum(g.polyline_length(pts, closed) for pts, closed in ent.polylines())
    if total <= 0:
        return None
    return _record(doc, "实体长度", total,
                   g.format_length(total, doc.units, doc.precision),
                   [p for pts, _ in ent.polylines() for p in pts[:1]],
                   extra=f"实体类型：{ent.kind}")


def format_line(m: dict) -> str:
    base = {"距离": "距离", "累计长度": "累计长度", "面积": "面积",
            "半径": "半径 / 直径", "实体长度": "实体长度"}.get(m["kind"], m["kind"])
    out = f"[{base}] {m['label']}"
    if m.get("extra"):
        out += f"\n    {m['extra']}"
    return out
