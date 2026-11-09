# SPDX-License-Identifier: GPL-3.0-or-later
"""Display-only alignment of title-block labels to the drawing's own grid.

LibreDWG's DXF writer loses TEXT group 73 (vertical justification) and the
MTEXT attachment is expressed in a font metric that does not match Qt's.
Both leave every title-block label about 0.3 * text_height away from the
centre of its cell, so labels sit on the grid lines and appear misaligned
even though the source DWG is correct.

The grid lines ARE part of the source drawing.  This module finds the smallest
rectangle formed by axis-aligned LINE / LWPOLYLINE segments around each label
and returns a display-only offset that centres the label in that cell.  The
source bytes, the imported DXF snapshot and all written DXF/DWG data are
untouched; this is purely a viewer transform stored in ``Entity.data``.
"""
from __future__ import annotations

from . import geometry as g

Point = tuple[float, float]


def _grid_lines(doc):
    hs: set[tuple[float, float, float]] = set()
    vs: set[tuple[float, float, float]] = set()
    for e in doc.entities:
        if e.kind == "LINE":
            segs = [(tuple(e.data["a"]), tuple(e.data["b"]))]
        elif e.kind in ("LWPOLYLINE", "POLYLINE"):
            pts = [tuple(p[:2]) for p in e.data.get("pts", [])]
            if len(pts) < 2:
                continue
            segs = list(zip(pts, pts[1:]))
            if e.data.get("closed") and len(pts) > 2:
                segs.append((pts[-1], pts[0]))
        else:
            continue
        for (x1, y1), (x2, y2) in segs:
            if abs(y1 - y2) < 1e-6 and abs(x2 - x1) > 1.0:
                hs.add((round(y1, 3), round(min(x1, x2), 3),
                        round(max(x1, x2), 3)))
            if abs(x1 - x2) < 1e-6 and abs(y2 - y1) > 1.0:
                vs.add((round(x1, 3), round(min(y1, y2), 3),
                        round(max(y1, y2), 3)))
    return sorted(hs), sorted(vs)


def _cell(p: Point, hs, vs):
    """Smallest grid cell containing p, or None."""
    ys = sorted({y for y, x0, x1 in hs if x0 - 1.0 <= p[0] <= x1 + 1.0})
    xs = sorted({x for x, y0, y1 in vs if y0 - 1.0 <= p[1] <= y1 + 1.0})
    if len(ys) < 2 or len(xs) < 2:
        return None
    below = [y for y in ys if y <= p[1] + 1.0]
    above = [y for y in ys if y >= p[1] - 1.0]
    left = [x for x in xs if x <= p[0] + 1.0]
    right = [x for x in xs if x >= p[0] - 1.0]
    if not (below and above and left and right):
        return None
    lo, hi = max(below), min(above)
    lx, rx = max(left), min(right)
    if hi - lo < 1.0 or rx - lx < 1.0:
        return None
    return (lx, lo, rx, hi)


def _visual_centre_y(p: Point, h: float, align: int, baseline: bool) -> float:
    """Approximate glyph-box centre in MODEL coordinates (y points up)."""
    vpos = (max(1, min(9, int(align))) - 1) // 3
    if vpos == 0:            # top attachment: glyphs hang below the anchor
        return p[1] - 0.55 * h
    if vpos == 1:            # middle
        return p[1]
    # bottom / baseline: glyphs rise above the anchor
    return p[1] + (0.30 if baseline else 0.55) * h


def _line_width(line: str, h: float) -> float:
    """Rough advance width: CJK glyphs ~1.0*h, Latin ~0.58*h."""
    return sum(1.0 if ord(ch) > 0x2E80 else 0.58 for ch in line) * h


def apply_title_block_alignment(doc, enabled: bool = True) -> int:
    """Store a display offset on every label inside a regular title-block grid.

    Returns the number of labels that were moved.  Calling with ``enabled``
    False clears all offsets (used by the viewer toggle).
    """
    for e in doc.entities:
        e.data.pop("display_offset", None)
    if not enabled:
        return 0
    hs, vs = _grid_lines(doc)
    if len({y for y, _, _ in hs}) < 3 or len({x for x, _, _ in vs}) < 3:
        return 0
    # Only the title-block corner of the sheet is a real grid.  Machine
    # outlines elsewhere in the drawing also form rectangles, and snapping a
    # dimension label to one of those would drag it hundreds of units away.
    frame = doc.sheet_frame_bounds()
    if not frame:
        return 0
    fw, fh = frame[2] - frame[0], frame[3] - frame[1]
    region = (frame[0] + 0.45 * fw, frame[1],
              frame[2], frame[1] + 0.45 * fh)
    grid_h = {y for y, x0, x1 in hs
              if region[0] <= x0 <= region[2] and region[1] <= y <= region[3]}
    grid_v = {x for x, y0, y1 in vs
              if region[0] <= x <= region[2] and region[1] <= y0 <= region[3]}
    if len(grid_h) < 4 or len(grid_v) < 4:
        return 0
    moved = 0
    for e in doc.entities:
        if e.kind not in ("TEXT", "MTEXT", "DIMENSION"):
            continue
        d = e.data
        p = tuple(d.get("p", (0.0, 0.0)))
        if not (region[0] <= p[0] <= region[2] and
                region[1] <= p[1] <= region[3]):
            continue
        h = float(d.get("height", 0.0)) * float(d.get("height_scale", 1.0) or 1.0)
        if h <= 0:
            continue
        lines = g.visible_text_lines(str(d.get("text", "")))
        if not lines:
            continue
        cell = _cell(p, hs, vs)
        if not cell:
            continue
        lx, lo, rx, hi = cell
        cw, ch = rx - lx, hi - lo
        if not (1.2 * h <= ch <= 30.0 * h and 0.8 * h <= cw <= 80.0 * h):
            continue
        cx, cy = (lx + rx) / 2.0, (lo + hi) / 2.0
        align = max(1, min(9, int(d.get("align", 1) or 1)))
        dy = cy - _visual_centre_y(p, h, align, bool(d.get("baseline")))
        dy = max(-0.35 * ch, min(0.35 * ch, dy))

        hpos = (align - 1) % 3
        width = max(_line_width(ln, h) for ln in lines)
        width *= float(d.get("width_factor", 1.0) or 1.0)
        width *= float(d.get("width_scale", 1.0) or 1.0)
        cur_cx = (p[0] + width / 2.0 if hpos == 0 else
                  p[0] if hpos == 1 else p[0] - width / 2.0)
        # Only short labels are centred horizontally.  A long company name
        # keeps its source insertion point: centring it would push the tail
        # outside the drawing frame (Qt's CJK glyphs are wider than the SHX
        # original, so even a conservative estimate can be off).
        dx = (cx - cur_cx) if width <= 0.55 * cw else 0.0
        dx = max(-0.35 * cw, min(0.35 * cw, dx))
        if abs(dx) > 1e-3 or abs(dy) > 1e-3:
            d["display_offset"] = (dx, dy)
            moved += 1
    return moved
