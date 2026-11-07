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
            pts = [tuple(p[:2]) for p in d.get("pts", [])]
            return [(pts, False)] if len(pts) > 1 else []
        if k == "HATCH":
            return [(list(path), True) for path in d.get("paths", []) if len(path) > 2]
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

    def snap_points(self) -> list[tuple[Point, str]]:
        d, k = self.data, self.kind
        out: list[tuple[Point, str]] = []
        for pts, _ in self.polylines():
            if not pts:
                continue
            out.append((tuple(pts[0]), "端点"))
            out.append((tuple(pts[-1]), "端点"))
            for i in range(len(pts) - 1):
                out.append((g.midpoint(tuple(pts[i]), tuple(pts[i + 1])), "中点"))
        if k in ("CIRCLE", "ARC"):
            c, r = tuple(d["c"]), float(d["r"])
            out.append((c, "圆心"))
            for a in (0, 90, 180, 270):
                out.append(((c[0] + r * math.cos(math.radians(a)),
                             c[1] + r * math.sin(math.radians(a))), "象限点"))
        if k in ("TEXT", "MTEXT", "DIMENSION"):
            out.append((tuple(d.get("p", (0, 0))), "插入点"))
        if k == "POINT":
            out.append((tuple(d["p"]), "点"))
        return out

    def bounds(self):
        b = None
        for pts, _ in self.polylines():
            b = g.expand_bounds(b, g.bounds_of(pts))
        if self.kind in ("TEXT", "MTEXT", "DIMENSION"):
            p = tuple(self.data.get("p", (0, 0)))
            h = float(self.data.get("height", 2.5)) or 2.5
            w = float(self.data.get("width", 0)) or len(str(self.data.get("text", ""))) * h * 0.7
            b = g.expand_bounds(b, (p[0], p[1] - h, p[0] + w, p[1] + h))
        return b

    # --------------------------------------------------------------- transform
    def transform(self, op: str, *args) -> "Entity":
        """op = translate(dx,dy) | rotate(origin,deg) | scale(origin,sx[,sy])"""
        d = self.data

        def px(p):
            p = tuple(p[:2])
            if op == "translate":
                return g.translate(p, args[0], args[1])
            if op == "rotate":
                return g.rotate(p, tuple(args[0]), args[1])
            if op == "scale":
                sy = args[3] if len(args) > 3 else args[2]
                return g.scale_point(p, tuple(args[0]), args[2], sy)
            return p

        def vec(p):
            p = tuple(p[:2])
            if op == "rotate":
                return g.rotate(p, (0.0, 0.0), args[1])
            if op == "scale":
                return (p[0] * args[2], p[1] * args[2])
            return p

        for key in ("a", "b", "c", "p"):
            if key in d and isinstance(d[key], (tuple, list)) and len(d[key]) >= 2:
                d[key] = px(d[key])

        if "pts" in d and isinstance(d["pts"], list):
            new_pts = []
            for item in d["pts"]:
                if isinstance(item, (tuple, list)) and len(item) >= 2:
                    q = px(item)
                    # LWPOLYLINE entries carry a bulge as a third element.
                    new_pts.append(q + tuple(item[2:]) if len(item) > 2 else q)
                else:
                    new_pts.append(item)
            d["pts"] = new_pts

        if "paths" in d and isinstance(d["paths"], list):
            d["paths"] = [[px(p) for p in path] for path in d["paths"]]

        if "major" in d:
            d["major"] = vec(d["major"])

        if op == "scale":
            for key in ("r", "height", "width"):
                if key in d and isinstance(d[key], (int, float)):
                    d[key] = float(d[key]) * args[2]

        if op == "rotate" and "rotation" in d:
            d["rotation"] = float(d.get("rotation", 0.0)) + args[1]

        return self


LINE = "LINE"
SUPPORTED_KINDS = {
    "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE",
    "POINT", "TEXT", "MTEXT", "SOLID", "SPLINE", "HATCH", "DIMENSION",
}
