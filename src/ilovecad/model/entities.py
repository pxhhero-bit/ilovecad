# SPDX-License-Identifier: GPL-3.0-or-later
"""2D entity representation.

Entities use a small, stable dictionary payload so that DXF import/export,
editing commands and both rendering paths share exactly the same data.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from . import geometry as g

Point = tuple[float, float]


@dataclass
class Entity:
    eid: int
    kind: str
    layer: str = "0"
    color: int = 256          # 256 = ByLayer
    linetype: str = "BYLAYER"
    lineweight: float = 0.0   # mm, 0 = ByLayer
    data: dict = field(default_factory=dict)
    selected: bool = False

    def clone(self) -> "Entity":
        return Entity(self.eid, self.kind, self.layer, self.color,
                      self.linetype, self.lineweight, copy.deepcopy(self.data),
                      self.selected)

    # ---------------------------------------------------------------- geometry
    def polylines(self) -> list[tuple[list[Point], bool]]:
        d, k = self.data, self.kind
        if k == "LINE":
            return [([tuple(d["a"]), tuple(d["b"])], False)]
        if k in ("LWPOLYLINE", "POLYLINE"):
            pts = [tuple(p[:2]) + (p[2] if len(p) > 2 else 0.0,) for p in d.get("pts", [])]
            return [(g.lwpolyline_points(pts, bool(d.get("closed"))), bool(d.get("closed")))]
        if k == "CIRCLE":
            return [(g.circle_points(tuple(d["c"]), float(d["r"])), True)]
        if k == "ARC":
            return [(g.arc_points(tuple(d["c"]), float(d["r"]),
                                  float(d["start"]), float(d["end"])), False)]
        if k == "ELLIPSE":
            return [(g.ellipse_points(tuple(d["c"]), tuple(d["major"]),
                                     float(d["ratio"]), float(d["start"]),
                                     float(d["end"])), False)]
        if k == "SOLID":
            pts = [tuple(p) for p in d.get("pts", [])]
            if len(pts) >= 3:
                order = [pts[0], pts[1], pts[2], pts[3] if len(pts) > 3 else pts[2]]
                return [(order, True)]
            return []
        if k == "SPLINE":
            # ``pts`` is the evaluated B-spline (see geometry.nurbs_points);
            # ``control`` keeps the original control polygon for snapping.
            pts = [tuple(p[:2]) for p in d.get("pts", [])]
            return [(pts, bool(d.get("closed")))] if len(pts) > 1 else []
        if k == "HATCH":
            # Boundary vertices carry an optional third element (bulge) for
            # the DXF writer; display geometry is strictly 2D and each loop is
            # closed independently. A HATCH is never a solid filled blob.
            return [(g.lwpolyline_points(
                        [tuple(p) if len(p) >= 3 else (p[0], p[1], 0.0)
                         for p in path], True), True)
                    for path in d.get("paths", []) if len(path) > 2]
        if k == "POINT":
            p = tuple(d["p"])
            return [([(p[0] - 0.5, p[1]), (p[0] + 0.5, p[1])], False),
                    ([(p[0], p[1] - 0.5), (p[0], p[1] + 0.5)], False)]
        return []

    def segments(self) -> list[tuple[Point, Point]]:
        out: list[tuple[Point, Point]] = []
        for pts, closed in self.polylines():
            out.extend(zip(pts, pts[1:]))
            if closed and len(pts) > 2:
                out.append((pts[-1], pts[0]))
        return out

    # ------------------------------------------------------------- snapping
    def snap_candidates(self) -> list[tuple[Point, str]]:
        """Semantic snap points taken from the RAW entity geometry.

        Tessellated display polylines must never be used here: a circle is
        drawn with 72 chords, but its only boundary points are the four
        quadrants.  Using the display polyline would create ~144 fake
        endpoints/midpoints all around the circle and drown out the real
        centre / midpoint snaps.  Returned keys are the stable ASCII kinds in
        tools.snapping.SNAP_KINDS.
        """
        d, k = self.data, self.kind
        out: list[tuple[Point, str]] = []

        def add(p, kind: str) -> None:
            if p is None:
                return
            out.append(((float(p[0]), float(p[1])), kind))

        def chain(pts: list[tuple], closed: bool = False,
                  bulges: bool = False) -> None:
            """Boundary vertices + one midpoint per real segment."""
            clean = [tuple(p[:2]) for p in pts if p is not None and len(p) >= 2]
            if not clean:
                return
            for p in clean:
                add(p, "endpoint")
            pairs = list(zip(clean, clean[1:]))
            if closed and len(clean) > 2:
                pairs.append((clean[-1], clean[0]))
            for i, (a, b) in enumerate(pairs):
                if bulges:
                    src = pts[i] if i < len(pts) else pts[-1]
                    bulge = float(src[2]) if len(src) > 2 else 0.0
                    arc = g.bulge_arc(a, b, bulge)
                    add(arc[len(arc) // 2], "midpoint")
                else:
                    add(g.midpoint(a, b), "midpoint")
            if closed and len(clean) >= 3:
                add(g.polygon_centroid(clean), "center")

        try:
            if k == "LINE":
                chain([tuple(d["a"]), tuple(d["b"])])
            elif k in ("LWPOLYLINE", "POLYLINE", "SPLINE"):
                raw = d.get("control") if k == "SPLINE" else None
                pts = [tuple(p) for p in (raw or d.get("pts", []))]
                chain(pts, bool(d.get("closed")), bulges=k != "SPLINE")
            elif k == "CIRCLE":
                c, r = tuple(d["c"]), float(d["r"])
                add(c, "center")
                for a in (0.0, 90.0, 180.0, 270.0):
                    add(g.point_on_circle(c, r, a), "quadrant")
            elif k == "ARC":
                c, r = tuple(d["c"]), float(d["r"])
                start = float(d["start"])
                sweep = g.arc_sweep(start, float(d["end"]))
                add(c, "center")
                add(g.point_on_circle(c, r, start), "endpoint")
                add(g.point_on_circle(c, r, start + sweep), "endpoint")
                add(g.point_on_circle(c, r, start + sweep / 2.0), "midpoint")
                for a in (0.0, 90.0, 180.0, 270.0):
                    if g.angle_in_arc(a, start, sweep):
                        add(g.point_on_circle(c, r, a), "quadrant")
            elif k == "ELLIPSE":
                c = tuple(d["c"])
                add(c, "center")
                start = float(d.get("start", 0.0))
                end = float(d.get("end", 2 * math.pi))
                curve = g.ellipse_points(c, tuple(d["major"]),
                                         float(d.get("ratio", 1.0)), start, end)
                if curve:
                    add(curve[0], "endpoint")
                    add(curve[-1], "endpoint")
                    add(curve[len(curve) // 2], "midpoint")
            elif k == "SOLID":
                pts = [tuple(p) for p in d.get("pts", []) if len(p) >= 2]
                if len(pts) >= 3:
                    order = [pts[0], pts[1], pts[2],
                             pts[3] if len(pts) > 3 else pts[2]]
                    chain(order, closed=True)
            elif k in ("TEXT", "MTEXT", "DIMENSION"):
                add(tuple(d.get("p", (0.0, 0.0))), "insertion")
            elif k == "POINT":
                add(tuple(d["p"]), "node")
        except (KeyError, TypeError, ValueError, IndexError):
            # A partially populated entity must never break cursor tracking.
            return []
        return out

    def snap_points(self) -> list[tuple[Point, str]]:
        """Backward-compatible view with human-readable Chinese labels."""
        labels = {"endpoint": "端点", "midpoint": "中点", "center": "中心",
                  "quadrant": "象限点", "intersection": "交点",
                  "perpendicular": "垂足", "nearest": "最近点",
                  "insertion": "插入点", "node": "点"}
        return [(p, labels.get(kind, kind)) for p, kind in self.snap_candidates()]

    def bounds(self, height_cap: float | None = None):
        b = None
        for pts, _ in self.polylines():
            b = g.expand_bounds(b, g.bounds_of(pts))
        if self.kind in ("TEXT", "MTEXT", "DIMENSION"):
            lines = g.visible_text_lines(str(self.data.get("text", "")))
            if not lines:
                return b
            p = tuple(self.data.get("p", (0, 0)))
            h = float(self.data.get("height", 2.5)) or 2.5
            if h <= 1e-6:          # zero-height placeholder attributes
                return b
            if height_cap and height_cap > 0:
                h = min(h, height_cap)
            # Group 41 of TEXT is a RELATIVE width factor; group 41 of MTEXT
            # is a column width in drawing units. Neither may be extrapolated
            # into a tens-of-thousands-of-units wide phantom bounding box.
            factor = float(self.data.get("width_factor",
                                         self.data.get("width", 1.0)) or 1.0)
            factor = max(0.1, min(factor, 5.0))
            char_count = max(1, min(max(len(ln.strip()) for ln in lines), 200))
            w = char_count * h * 0.6 * factor
            column = float(self.data.get("column_width", 0.0) or 0.0)
            if column > h:
                w = min(w, column)
            w = min(w, h * 400.0)
            n = max(1, min(len(lines), 60))
            total_h = n * h * 1.25
            attach = int(self.data.get("align", 1) or 1)
            hpos = (attach - 1) % 3          # 0 left, 1 centre, 2 right
            x0 = p[0] - (w / 2 if hpos == 1 else w if hpos == 2 else 0.0)
            x1 = x0 + w
            if self.data.get("mtext"):
                vpos = (attach - 1) // 3      # 0 top, 1 middle, 2 bottom
                if vpos == 0:
                    y0, y1 = p[1] - total_h, p[1] + 0.3 * h
                elif vpos == 1:
                    y0, y1 = p[1] - total_h / 2, p[1] + total_h / 2
                else:
                    y0, y1 = p[1] - 0.3 * h, p[1] + total_h
            else:
                y0, y1 = p[1] - h, p[1] + h
            b = g.expand_bounds(b, (x0, y0, x1, y1))
        return b

    # --------------------------------------------------------------- transform
    def transform(self, op: str, *args) -> "Entity":
        """op = translate(dx,dy) | rotate(origin,deg) | scale(origin,sx[,sy])
        | mirror(a,b)   — mirror reflects about the infinite line through a-b.
        """
        d = self.data
        # scale() is called as (origin, sx) or (origin, sx, sy). Earlier code
        # indexed args[2] unconditionally, which crashed the SC command on
        # every single-factor scale.
        sx = args[1] if len(args) > 1 else 1.0
        sy = args[2] if len(args) > 2 else sx

        def px(p):
            p = tuple(p[:2])
            if op == "translate":
                return g.translate(p, args[0], args[1])
            if op == "rotate":
                return g.rotate(p, tuple(args[0]), args[1])
            if op == "scale":
                return g.scale_point(p, tuple(args[0]), sx, sy)
            if op == "mirror":
                return g.mirror_point(p, tuple(args[0]), tuple(args[1]))
            return p

        def vec(p):
            p = tuple(p[:2])
            if op == "rotate":
                return g.rotate(p, (0.0, 0.0), args[1])
            if op == "scale":
                return (p[0] * sx, p[1] * sy)
            if op == "mirror":
                a, b = tuple(args[0]), tuple(args[1])
                return g.mirror_direction(p, (0.0, 0.0),
                                          (b[0] - a[0], b[1] - a[1]))
            return p

        for key in ("a", "b", "c", "p"):
            if key in d and isinstance(d[key], (tuple, list)) and len(d[key]) >= 2:
                d[key] = px(d[key])

        for key in ("pts", "control"):
            if key in d and isinstance(d[key], list):
                new_pts = []
                for item in d[key]:
                    if isinstance(item, (tuple, list)) and len(item) >= 2:
                        q = px(item)
                        tail = tuple(item[2:])
                        if op == "mirror" and tail and isinstance(tail[0], (int, float)):
                            # Bulge encodes arc direction; a reflection reverses it.
                            tail = (-float(tail[0]),) + tail[1:]
                        new_pts.append(q + tail if tail else q)
                    else:
                        new_pts.append(item)
                d[key] = new_pts

        if "paths" in d and isinstance(d["paths"], list):
            d["paths"] = [[px(p) for p in path] for path in d["paths"]]

        if "major" in d:
            d["major"] = vec(d["major"])

        if op == "scale":
            for key in ("r", "height", "width"):
                if key in d and isinstance(d[key], (int, float)):
                    d[key] = float(d[key]) * sx

        if op == "rotate" and "rotation" in d:
            d["rotation"] = float(d.get("rotation", 0.0)) + args[1]

        if op == "mirror":
            a, b = tuple(args[0]), tuple(args[1])
            axis = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if self.kind == "ARC" and "start" in d and "end" in d:
                start, end = float(d["start"]), float(d["end"])
                sweep = g.arc_sweep(start, end)
                new_start = (2.0 * axis - end) % 360.0
                d["start"], d["end"] = new_start, new_start + sweep
            elif self.kind == "ELLIPSE" and "start" in d and "end" in d:
                start, end = float(d["start"]), float(d["end"])
                d["start"], d["end"] = -end, -start
            if "rotation" in d:
                d["rotation"] = 2.0 * axis - float(d.get("rotation", 0.0))

        return self


LINE = "LINE"
SUPPORTED_KINDS = {
    "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE",
    "POINT", "TEXT", "MTEXT", "SOLID", "SPLINE", "HATCH", "DIMENSION",
}
