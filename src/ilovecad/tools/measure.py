# SPDX-License-Identifier: GPL-3.0-or-later
"""2D measurement commands and unit-aware formatting."""
from __future__ import annotations

import math

from ..model import geometry as g
from ..model.document import DwgDocument
from ..model.entities import Entity

Point = tuple[float, float]


def _record(doc: DwgDocument, kind: str, value: float, label: str,
            points: list[Point], closed: bool = False, extra: str = "",
            paths: list[list[Point]] | None = None,
            label_at: Point | None = None) -> dict:
    m = {"kind": kind, "value": value, "label": label,
         "points": [tuple(p) for p in points], "closed": closed, "extra": extra}
    if paths:
        m["paths"] = [[tuple(p) for p in seq] for seq in paths]
    if label_at is not None:
        m["label_at"] = tuple(label_at)
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


def _ray_angle_data(vertex: Point, p1: Point, p2: Point):
    """Return (azimuth1, azimuth2, arc_start, arc_sweep) for the smaller angle.

    Both screen and PDF rendering flip the Y axis back to mathematical
    orientation, so a positive sweep here is always counter-clockwise on paper.
    """
    a1 = g.angle_deg(vertex, p1)
    a2 = g.angle_deg(vertex, p2)
    ccw = (a2 - a1) % 360.0
    if ccw <= 180.0:
        return a1, a2, a1, ccw
    return a1, a2, a2, 360.0 - ccw


def _angle_annotation(doc: DwgDocument, vertex: Point, start: float, sweep: float,
                      radius: float) -> tuple[list[Point], Point]:
    """Build the angle arc plus a suitable label anchor just outside it."""
    arc = g.arc_points(vertex, radius, start, start + sweep)
    label_at = g.rotate((vertex[0] + radius * 1.38, vertex[1]),
                        vertex, start + sweep / 2.0)
    return arc, label_at


def angle_three_points(doc: DwgDocument, vertex: Point, p1: Point,
                       p2: Point) -> dict | None:
    """Measure the included angle of three points: vertex, ray 1, ray 2."""
    l1, l2 = g.dist(vertex, p1), g.dist(vertex, p2)
    if l1 < g.EPS or l2 < g.EPS:
        return None
    a1, a2, start, sweep = _ray_angle_data(vertex, p1, p2)
    shorter = min(l1, l2)
    arc, label_at = _angle_annotation(
        doc, vertex, start, sweep, max(shorter * 0.32, 1e-9))
    # Short arms rather than full rays: a long annotation would otherwise
    # recolour the underlying drawing all the way to the picked points.
    arm = max(shorter * 0.42, 1e-9)
    e1 = (vertex[0] + arm * math.cos(math.radians(a1)),
          vertex[1] + arm * math.sin(math.radians(a1)))
    e2 = (vertex[0] + arm * math.cos(math.radians(a2)),
          vertex[1] + arm * math.sin(math.radians(a2)))
    extra = (f"补角={180.0 - sweep:.2f}°  弧度={math.radians(sweep):.{doc.precision}f} rad"
             f"\n    边1 方位={a1:.2f}°  长度={g.format_length(l1, doc.units, doc.precision)}"
             f"\n    边2 方位={a2:.2f}°  长度={g.format_length(l2, doc.units, doc.precision)}"
             f"\n    顶点=({vertex[0]:.{doc.precision}f}, {vertex[1]:.{doc.precision}f})")
    return _record(doc, "夹角", sweep, f"∠ {sweep:.2f}°", arc,
                   paths=[[vertex, e1], [vertex, e2], arc],
                   label_at=label_at, extra=extra)


def _longest_segment(ent: Entity):
    segs = sorted(ent.segments(), key=lambda s: g.dist(s[0], s[1]), reverse=True)
    return segs[0] if segs else None


def angle_between_lines(doc: DwgDocument, ent1: Entity,
                        ent2: Entity) -> dict | None:
    """Measure the included angle of two picked line-like entities."""
    s1, s2 = _longest_segment(ent1), _longest_segment(ent2)
    if not s1 or not s2:
        return None
    (a1, b1), (a2, b2) = s1, s2
    d1 = (b1[0] - a1[0], b1[1] - a1[1])
    d2 = (b2[0] - a2[0], b2[1] - a2[1])
    n1, n2 = math.hypot(*d1), math.hypot(*d2)
    if n1 < g.EPS or n2 < g.EPS:
        return None
    ang1 = math.degrees(math.atan2(d1[1], d1[0]))
    ang2 = math.degrees(math.atan2(d2[1], d2[0]))
    ccw = (ang2 - ang1) % 360.0
    start, sweep = (ang1, ccw) if ccw <= 180.0 else (ang2, 360.0 - ccw)

    c1, c2 = g.midpoint(a1, b1), g.midpoint(a2, b2)
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    note = ""
    if abs(cross) > g.EPS:
        t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / cross
        vertex = (a1[0] + t * d1[0], a1[1] + t * d1[1])
        limit = 3.0 * max(n1, n2)
        if g.dist(vertex, c1) > limit or g.dist(vertex, c2) > limit:
            vertex, note = g.midpoint(c1, c2), "（交点在延长线上，标注置于线段中点）"
    else:
        vertex, note = g.midpoint(c1, c2), "（两线平行或重合）"

    arm = max(min(n1, n2) * 0.5, g.EPS)
    u1 = (math.cos(math.radians(start)), math.sin(math.radians(start)))
    u2 = (math.cos(math.radians(start + sweep)),
          math.sin(math.radians(start + sweep)))
    e1 = (vertex[0] + u1[0] * arm, vertex[1] + u1[1] * arm)
    e2 = (vertex[0] + u2[0] * arm, vertex[1] + u2[1] * arm)
    arc, label_at = _angle_annotation(doc, vertex, start, sweep, arm * 0.62)
    extra = (f"补角={180.0 - sweep:.2f}°  {ent1.kind} 方位={ang1:.2f}°"
             f"  {ent2.kind} 方位={ang2:.2f}°"
             f"\n    交点=({vertex[0]:.{doc.precision}f}, "
             f"{vertex[1]:.{doc.precision}f})"
             + (f"  {note}" if note else ""))
    return _record(doc, "夹角", sweep, f"∠ {sweep:.2f}°", arc,
                   paths=[[vertex, e1], [vertex, e2], arc],
                   label_at=label_at, extra=extra)


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
            "半径": "半径 / 直径", "实体长度": "实体长度",
            "夹角": "夹角"}.get(m["kind"], m["kind"])
    out = f"[{base}] {m['label']}"
    if m.get("extra"):
        out += f"\n    {m['extra']}"
    return out
