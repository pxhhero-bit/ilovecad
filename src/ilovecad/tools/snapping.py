# SPDX-License-Identifier: GPL-3.0-or-later
"""2D object snapping: endpoint, midpoint, centre, intersection, nearest, perpendicular."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..model import geometry as g
from ..model.document import DwgDocument

Point = tuple[float, float]


@dataclass
class SnapResult:
    point: Point
    label: str
    distance: float


def _near_bounds(b, p: Point, tol: float) -> bool:
    if b is None:
        return True
    return (b[0] - tol <= p[0] <= b[2] + tol and
            b[1] - tol <= p[1] <= b[3] + tol)


def find_snap(doc: DwgDocument, p: Point, tol: float,
              enabled: bool = True) -> SnapResult | None:
    if not enabled or tol <= 0:
        return None
    best: SnapResult | None = None
    nearby_segments: list[tuple[Point, Point]] = []

    def consider(q: Point, label: str, weight: float = 1.0):
        nonlocal best
        d = g.dist(p, q) * weight
        if d <= tol and (best is None or d < best.distance):
            best = SnapResult(tuple(q), label, d)

    for ent in doc.entities:
        if not doc.visible(ent) or not _near_bounds(ent.bounds(), p, tol * 2):
            continue
        for q, label in ent.snap_points():
            consider(q, label, 0.85 if label in ("端点", "圆心", "交点") else 1.0)
        for a, b in ent.segments():
            if len(nearby_segments) < 400:
                nearby_segments.append((a, b))
            consider(g.closest_on_segment(p, a, b), "最近点", 1.05)

    # Intersections of nearby segments.
    n = min(len(nearby_segments), 120)
    for i in range(n):
        a1, a2 = nearby_segments[i]
        for j in range(i + 1, n):
            b1, b2 = nearby_segments[j]
            if not _near_bounds(g.bounds_of([a1, a2]), p, tol) and \
               not _near_bounds(g.bounds_of([b1, b2]), p, tol):
                continue
            q = g.segment_intersection(a1, a2, b1, b2)
            if q:
                consider(q, "交点", 0.8)

    # Perpendicular: snap the point onto the nearest segment direction.
    if best is None:
        for a, b in nearby_segments[:80]:
            q = g.closest_on_segment(p, a, b)
            consider(q, "垂足", 1.2)
    return best


def ortho_point(origin: Point, p: Point) -> Point:
    dx, dy = p[0] - origin[0], p[1] - origin[1]
    return (p[0], origin[1]) if abs(dx) >= abs(dy) else (origin[0], p[1])


def nearest_entity(doc: DwgDocument, p: Point, tol: float):
    best, best_d = None, float("inf")
    for ent in doc.entities:
        if not doc.visible(ent) or not _near_bounds(ent.bounds(), p, tol * 2):
            continue
        for a, b in ent.segments():
            d = g.point_segment_distance(p, a, b)
            if d < best_d:
                best_d, best = d, ent
    return best if best_d <= tol else None


def entities_in_rect(doc: DwgDocument, rect):
    x0, y0, x1, y1 = rect
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    ids = set()
    for ent in doc.entities:
        if not doc.visible(ent):
            continue
        b = ent.bounds()
        if b and b[0] >= x0 and b[2] <= x1 and b[1] >= y0 and b[3] <= y1:
            ids.add(ent.eid)
    return ids
