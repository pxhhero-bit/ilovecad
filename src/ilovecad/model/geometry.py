# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure 2D geometry helpers. No Qt, no LibreDWG, no 3D."""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

Point = tuple[float, float]
EPS = 1e-9


def dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def angle_deg(a: Point, b: Point) -> float:
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def rotate(p: Point, origin: Point, deg: float) -> Point:
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    x, y = p[0] - origin[0], p[1] - origin[1]
    return (origin[0] + x * c - y * s, origin[1] + x * s + y * c)


def translate(p: Point, dx: float, dy: float) -> Point:
    return (p[0] + dx, p[1] + dy)


def scale_point(p: Point, origin: Point, sx: float, sy: float | None = None) -> Point:
    sy = sx if sy is None else sy
    return (origin[0] + (p[0] - origin[0]) * sx, origin[1] + (p[1] - origin[1]) * sy)


def polyline_length(points: Sequence[Point], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = sum(dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    if closed:
        total += dist(points[-1], points[0])
    return total


def polygon_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def closest_on_segment(p: Point, a: Point, b: Point) -> Point:
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    if den < EPS:
        return a
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den
    t = max(0.0, min(1.0, t))
    return (a[0] + t * dx, a[1] + t * dy)


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    return dist(p, closest_on_segment(p, a, b))


def segment_intersection(p1: Point, p2: Point, p3: Point, p4: Point) -> Point | None:
    d1 = (p2[0] - p1[0], p2[1] - p1[1])
    d2 = (p4[0] - p3[0], p4[1] - p3[1])
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < EPS:
        return None
    t = ((p3[0] - p1[0]) * d2[1] - (p3[1] - p1[1]) * d2[0]) / den
    u = ((p3[0] - p1[0]) * d1[1] - (p3[1] - p1[1]) * d1[0]) / den
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return (p1[0] + t * d1[0], p1[1] + t * d1[1])
    return None


def circle_points(c: Point, r: float, segments: int = 72) -> list[Point]:
    return [(c[0] + r * math.cos(2 * math.pi * i / segments),
             c[1] + r * math.sin(2 * math.pi * i / segments)) for i in range(segments + 1)]


def arc_points(c: Point, r: float, start_deg: float, end_deg: float) -> list[Point]:
    while end_deg < start_deg:
        end_deg += 360.0
    sweep = end_deg - start_deg
    if sweep > 360.0:
        sweep = 360.0
    n = max(6, int(sweep / 4.0))
    return [(c[0] + r * math.cos(math.radians(start_deg + sweep * i / n)),
             c[1] + r * math.sin(math.radians(start_deg + sweep * i / n))) for i in range(n + 1)]


def ellipse_points(c: Point, major: Point, ratio: float,
                   start_rad: float, end_rad: float) -> list[Point]:
    mx, my = major
    nx, ny = -my * ratio, mx * ratio
    while end_rad < start_rad:
        end_rad += 2 * math.pi
    sweep = min(end_rad - start_rad, 2 * math.pi)
    n = max(12, int(math.degrees(sweep) / 4.0))
    return [(c[0] + mx * math.cos(t) + nx * math.sin(t),
             c[1] + my * math.cos(t) + ny * math.sin(t))
            for i in range(n + 1)
            for t in [start_rad + sweep * i / n]]


def bulge_arc(p1: Point, p2: Point, bulge: float) -> list[Point]:
    """Approximate an LWPOLYLINE bulge segment with a polyline."""
    if abs(bulge) < 1e-10:
        return [p1, p2]
    theta = 4.0 * math.atan(bulge)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    if chord < EPS:
        return [p1, p2]
    half = abs(theta) / 2.0
    if abs(math.sin(half)) < EPS:
        return [p1, p2]
    r = chord / (2.0 * math.sin(half))
    d2 = r * r - (chord / 2.0) ** 2
    d = math.sqrt(max(d2, 0.0)) * (1.0 if bulge > 0 else -1.0)
    ux, uy = dx / chord, dy / chord
    cx = (p1[0] + p2[0]) / 2.0 + d * (-uy)
    cy = (p1[1] + p2[1]) / 2.0 + d * ux
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    n = max(6, int(abs(math.degrees(theta)) / 5.0))
    return [(cx + r * math.cos(a1 + theta * i / n),
             cy + r * math.sin(a1 + theta * i / n)) for i in range(n + 1)]


def lwpolyline_points(pts: Sequence[tuple[float, float, float]], closed: bool) -> list[Point]:
    """pts entries are (x, y, bulge)."""
    if not pts:
        return []
    out: list[Point] = [(pts[0][0], pts[0][1])]
    pairs = list(zip(pts, pts[1:]))
    if closed:
        pairs.append((pts[-1], pts[0]))
    for a, b in pairs:
        seg = bulge_arc((a[0], a[1]), (b[0], b[1]), a[2])
        out.extend(seg[1:])
    return out


def bounds_of(points: Iterable[Point]) -> tuple[float, float, float, float] | None:
    xs, ys = [], []
    for x, y in points:
        if math.isfinite(x) and math.isfinite(y):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def expand_bounds(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


_UCODE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")


def decode_cad_text(text: str) -> str:
    """Handle the common DXF/DWG text escapes without any font dependency."""
    if not text:
        return ""
    text = _UCODE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("\\P", "\n").replace("\\p", "\n")
    text = re.sub(r"\\[AaFfHhWwQqTtCc][0-9.;+_-]*", "", text)
    text = text.replace("%%c", "⌀").replace("%%d", "°").replace("%%p", "±")
    text = text.replace("%%C", "⌀").replace("%%D", "°").replace("%%P", "±")
    text = text.replace("\\~", " ")
    return text.strip()


def format_length(value: float, units: int, precision: int = 4) -> str:
    label = UNIT_LABELS.get(units, "单位")
    return f"{value:.{precision}f} {label}"


def format_area(value: float, units: int, precision: int = 4) -> str:
    label = UNIT_LABELS.get(units, "单位")
    return f"{value:.{precision}f} {label}²"


UNIT_LABELS = {
    0: "无单位", 1: "in", 2: "ft", 3: "mi", 4: "mm", 5: "cm",
    6: "m", 7: "km", 8: "µin", 9: "mil", 10: "yd", 11: "Å",
    12: "nm", 13: "µm", 14: "dm", 15: "dam", 16: "hm",
}
# Millimetres per drawing unit, used by the PDF scale calculation.
UNIT_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0,
           7: 1e6, 10: 914.4, 13: 0.001, 14: 100.0}
