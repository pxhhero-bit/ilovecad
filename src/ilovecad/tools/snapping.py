# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed 2D object snapping.

Snap candidates come from semantic entity geometry (real vertices, real
segment midpoints, circle / arc / ellipse centres, closed-shape centroids),
never from the tessellated polylines used for drawing.  Every candidate kind
can be switched on and off independently and has its own priority, so a
nearby endpoint does not get stolen by a generic nearest-point snap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import geometry as g
from ..model.document import DwgDocument

Point = tuple[float, float]

# ASCII keys are stable in the local settings file; values are UI labels.
KIND_LABELS = {
    "endpoint": "端点 / 顶点",       # boundary points of every 2D entity
    "midpoint": "中点",
    "center": "中心 / 圆心",
    "quadrant": "象限点",
    "intersection": "交点",
    "perpendicular": "垂足",
    "nearest": "最近点",
    "insertion": "插入点",
    "node": "节点",
}
SNAP_KINDS = tuple(KIND_LABELS)
DEFAULT_MODES = {kind: True for kind in SNAP_KINDS}

# Lower weight = higher priority when two candidates are equally close.
PRIORITY = {
    "endpoint": 0.72,
    "intersection": 0.78,
    "center": 0.82,
    "node": 0.84,
    "midpoint": 0.88,
    "quadrant": 0.94,
    "insertion": 0.98,
    "perpendicular": 1.08,
    "nearest": 1.22,
}

MARKER_SHAPES = {
    "endpoint": "square",
    "midpoint": "triangle",
    "center": "circle",
    "quadrant": "diamond",
    "intersection": "cross",
    "perpendicular": "angle",
    "nearest": "hourglass",
    "insertion": "plus",
    "node": "dot",
}


@dataclass
class SnapConfig:
    enabled: bool = True
    radius_px: float = 12.0
    modes: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_MODES))

    def __post_init__(self) -> None:
        self.radius_px = max(3.0, min(40.0, float(self.radius_px)))
        merged = dict(DEFAULT_MODES)
        merged.update({k: bool(v) for k, v in (self.modes or {}).items()
                       if k in DEFAULT_MODES})
        self.modes = merged

    def allows(self, kind: str) -> bool:
        return self.enabled and self.modes.get(kind, False)

    def copy(self) -> "SnapConfig":
        return SnapConfig(self.enabled, self.radius_px, dict(self.modes))

    def active_kinds(self) -> list[str]:
        return [k for k in SNAP_KINDS if self.modes.get(k)]


@dataclass
class SnapResult:
    point: Point
    label: str
    distance: float                 # real model-space distance to the cursor
    kind: str = "nearest"           # stable ASCII key
    score: float = 0.0              # priority-weighted distance used for ranking
    eid: int | None = None


def _near_bounds(b, p: Point, tol: float) -> bool:
    if b is None:
        return True
    return (b[0] - tol <= p[0] <= b[2] + tol and
            b[1] - tol <= p[1] <= b[3] + tol)


def find_snap(doc: DwgDocument, p: Point, tol: float, enabled: bool = True,
              config: SnapConfig | None = None,
              reference: Point | None = None) -> SnapResult | None:
    """Find the highest-priority snap point within ``tol`` model units.

    ``reference`` is the last picked point; when present, perpendicular snaps
    project from that point onto nearby segments (CAD-style PER).  Without a
    reference the perpendicular mode simply stays quiet instead of pretending
    that a generic nearest point is a foot.
    """
    cfg = config or SnapConfig()
    if not enabled or not cfg.enabled or tol <= 0:
        return None
    best: SnapResult | None = None
    segments: list[tuple[float, Point, Point, int]] = []

    def consider(q: Point, kind: str, eid: int | None = None) -> None:
        nonlocal best
        if not cfg.allows(kind):
            return
        d = g.dist(p, q)
        if d > tol:
            return
        score = d * PRIORITY[kind]
        if best is None or (score, d) < (best.score, best.distance):
            best = SnapResult((float(q[0]), float(q[1])),
                              KIND_LABELS[kind], d, kind, score, eid)

    for ent in doc.entities:
        if not doc.visible(ent) or not _near_bounds(ent.bounds(), p, tol * 2):
            continue
        for q, kind in ent.snap_candidates():
            consider(q, kind, ent.eid)
        for a, b in ent.segments():
            distance = g.point_segment_distance(p, a, b)
            if distance <= tol * 2 and len(segments) < 600:
                segments.append((distance, a, b, ent.eid))
            if cfg.allows("nearest"):
                consider(g.closest_on_segment(p, a, b), "nearest", ent.eid)

    segments.sort(key=lambda item: item[0])
    nearby = segments[:120]

    if cfg.allows("intersection"):
        for i, (_, a1, a2, eid1) in enumerate(nearby):
            for _, b1, b2, eid2 in nearby[i + 1:]:
                if eid1 == eid2:
                    # Adjacent chords of one circle/polyline must not become
                    # bogus "intersections" at their shared vertex.
                    continue
                if not _near_bounds(g.bounds_of([a1, a2]), p, tol) or \
                   not _near_bounds(g.bounds_of([b1, b2]), p, tol):
                    continue
                q = g.segment_intersection(a1, a2, b1, b2)
                if q:
                    consider(q, "intersection")

    if cfg.allows("perpendicular") and reference is not None:
        for _, a, b, eid in nearby[:80]:
            # Projection of the previous point onto the segment: the vector
            # (foot - reference) is perpendicular to the segment by construction.
            consider(g.closest_on_segment(reference, a, b), "perpendicular", eid)

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
