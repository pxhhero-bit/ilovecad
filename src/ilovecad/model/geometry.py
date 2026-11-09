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


def mirror_point(p: Point, a: Point, b: Point) -> Point:
    """Reflect a point about the infinite line through a and b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n2 = dx * dx + dy * dy
    if n2 < EPS:
        return (float(p[0]), float(p[1]))
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / n2
    foot = (a[0] + t * dx, a[1] + t * dy)
    return (2 * foot[0] - p[0], 2 * foot[1] - p[1])


def mirror_direction(v: Point, a: Point, b: Point) -> Point:
    """Reflect a free vector (e.g. an ellipse major axis) about line a-b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n2 = dx * dx + dy * dy
    if n2 < EPS:
        return (float(v[0]), float(v[1]))
    dot = (v[0] * dx + v[1] * dy) / n2
    return (2 * dot * dx - v[0], 2 * dot * dy - v[1])


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


def polygon_centroid(points: Sequence[Point]) -> Point | None:
    """Area centroid of a polygon; falls back to the bounds centre if degenerate."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        return None
    twice_area = 0.0
    cx = cy = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        cross = x1 * y2 - x2 * y1
        twice_area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(twice_area) < EPS:
        b = bounds_of(pts)
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) if b else None
    return (cx / (3.0 * twice_area), cy / (3.0 * twice_area))


def arc_sweep(start_deg: float, end_deg: float) -> float:
    """Normalised counter-clockwise sweep of an ARC (0..360 degrees)."""
    while end_deg < start_deg:
        end_deg += 360.0
    return min(end_deg - start_deg, 360.0)


def point_on_circle(c: Point, r: float, deg: float) -> Point:
    return (c[0] + r * math.cos(math.radians(deg)),
            c[1] + r * math.sin(math.radians(deg)))


def angle_in_arc(deg: float, start_deg: float, sweep: float) -> bool:
    """Whether a circle angle lies inside the ARC sweep (degrees)."""
    return 0.0 <= (deg - start_deg) % 360.0 <= sweep + EPS


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
# An MTEXT font descriptor such as {\fSimHei|b0|i0|c134|p49;DN15气动阀}
# must contribute ONLY "DN15气动阀".  The old single-letter escape regex
# stopped at the first letter of the font name and left the rest of the
# control sequence on screen (the "{HY..." / "|b0|i0|c1|p0;\o\l" noise).
_MTEXT_GROUPS = re.compile(
    r"\\[fFhHwWcCqQtTaA][^;\\]*;|"      # font / height / width / colour / alignment
    r"\\[pP][xXiIlLcCrR0-9.;+_-]*;|"   # paragraph / indent controls
    r"\\[oOlLkKnNxX]|"                 # underline / overline / strike toggles
    r"\\[AaFfHhWwQqTtCc][0-9.;+_-]*|"
    r"\|[A-Za-z]\d*")                  # |b0 |i0 |c134 |p49 font attributes
_STACK = re.compile(r"\\S([^;]*);")
_LEADING_JUNK = re.compile(r"^[;{}\s]+")


def decode_cad_text(text: str) -> str:
    """Turn DXF/DWG text into plain, printable lines without font dependency.

    Handles \\U+XXXX, MTEXT format groups, paragraph breaks, stacked fractions,
    AutoCAD %% escapes and LibreDWG's leftover SHX control fragments.  Newlines
    are preserved: a multi-line MTEXT must never be flattened into one endless
    line across the sheet.
    """
    if not text:
        return ""
    text = _UCODE.sub(lambda m: chr(int(m.group(1), 16)), text)
    text = text.replace("\\P", "\n").replace("\\p", "\n")
    # Stacked fractions keep their human-readable numerator/denominator.
    text = _STACK.sub(lambda m: m.group(1).replace("^", " ").replace("/", " / ")
                      .replace("#", " / "), text)
    text = _MTEXT_GROUPS.sub("", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\~", " ")
    text = re.sub(r"\\[AaFfHhWwQqTtCc][0-9.;+_-]*", "", text)
    text = re.sub(r"\\.", "", text)        # any remaining single-letter escape
    text = text.replace("%%c", "⌀").replace("%%d", "°").replace("%%p", "±")
    text = text.replace("%%C", "⌀").replace("%%D", "°").replace("%%P", "±")
    out = []
    for line in text.split("\n"):
        line = _LEADING_JUNK.sub("", line)
        line = re.sub(r"[;{}]+$", "", line).strip()
        out.append(line)
    return "\n".join(out).strip()


_FORMAT_MARKER = re.compile(
    r"\\U\+|\\P|\\p|\\[fFhHwWcCqQtTaA]|\{\\|%%[cdpCDP]|\\S|\|b\d|\\o\\l")
_HEIGHT_RELATIVE = re.compile(r"\\H([0-9]*\.?[0-9]+)\s*x", re.IGNORECASE)
_HEIGHT_ABSOLUTE = re.compile(r"\\H([0-9]*\.?[0-9]+)\s*;", re.IGNORECASE)
_WIDTH_SCALE = re.compile(r"\\W([0-9]*\.?[0-9]+)", re.IGNORECASE)
_OBLIQUE = re.compile(r"\\Q([-+0-9]*\.?[0-9]+)", re.IGNORECASE)
_TRACKING = re.compile(r"\\T([0-9]*\.?[0-9]+)", re.IGNORECASE)
_COLOR = re.compile(r"\\C([0-9]+)", re.IGNORECASE)
_FONT = re.compile(r"\\[fF]([^;\\]*);")
_ALIGN = re.compile(r"\\A([0-9]+)", re.IGNORECASE)


def parse_mtext_format(raw: str) -> dict:
    """Extract semantic MTEXT overrides BEFORE the printable text is stripped.

    ``\\H0.8333x`` / ``\\W1.4`` / ``\\Q15`` / ``\\C4`` / ``\\fSimHei|b1|i0;``
    carry the relative size, width, slant, colour, font family and bold /
    italic state of a label.  Dropping them made every label look identical.
    """
    raw = str(raw or "")
    out: dict = {"inline_font": "", "bold": False, "italic": False,
                 "height_scale": 1.0, "absolute_height": 0.0,
                 "width_scale": 1.0, "oblique": 0.0, "tracking": 1.0,
                 "color_index": None, "underline": False,
                 "strikeout": False, "align": None}
    font_match = _FONT.search(raw)
    if font_match:
        parts = font_match.group(1).split("|")
        out["inline_font"] = parts[0].strip()
        for flag in parts[1:]:
            flag = flag.strip().lower()
            if flag.startswith("b") and flag[1:2] == "1":
                out["bold"] = True
            elif flag.startswith("i") and flag[1:2] == "1":
                out["italic"] = True
    rels = _HEIGHT_RELATIVE.findall(raw)
    if rels:
        # The LAST \H in force is the one that applies to the visible text
        # (e.g. "\H1.5x;\fKaiTi…;\H0.8333x;技术要求").
        out["height_scale"] = max(0.05, min(20.0, float(rels[-1])))
    else:
        absolute = _HEIGHT_ABSOLUTE.search(raw)
        if absolute:
            out["absolute_height"] = float(absolute.group(1))
    widths = _WIDTH_SCALE.findall(raw)
    if widths:
        out["width_scale"] = max(0.05, min(20.0, float(widths[-1])))
    oblique = _OBLIQUE.search(raw)
    if oblique:
        out["oblique"] = max(-85.0, min(85.0, float(oblique.group(1))))
    tracking = _TRACKING.search(raw)
    if tracking:
        out["tracking"] = max(0.1, min(10.0, float(tracking.group(1))))
    color = _COLOR.search(raw)
    if color:
        out["color_index"] = int(color.group(1))
    align = _ALIGN.search(raw)
    if align:
        out["align"] = int(align.group(1))
    out["underline"] = "\\L" in raw or "\\O" in raw
    out["strikeout"] = "\\K" in raw
    return out


def adaptive_text_height(height: float, cap: float | None) -> float:
    """Rank-preserving compression for pathologically large sheet titles.

    A hard clamp flattened every large label to exactly the same size and
    destroyed the drawing's type hierarchy (the exact complaint this fixes).
    Above the cap we compress only the EXCESS by 97%, so order and visible
    differences between 600 / 1900 / 3300 / 4200-unit labels are preserved
    without letting a few title-block MTEXTs cover the whole sheet.
    ``cap=None`` means exact 1:1 source heights (the "原样" setting).
    """
    if height <= 0 or not cap or cap <= 0 or height <= cap:
        return height
    return cap + (height - cap) * 0.03


def wrap_text_lines(lines, max_chars: int) -> list[str]:
    """Wrap MTEXT lines to its reference rectangle width (character units)."""
    if max_chars <= 0:
        return list(lines)
    out: list[str] = []
    for line in lines:
        if len(line) <= max_chars:
            out.append(line)
            continue
        buf: list[str] = []
        width = 0
        for ch in line:
            cw = 2 if ord(ch) > 0x2E80 else 1
            if width + cw > max_chars and buf:
                out.append("".join(buf).rstrip())
                buf, width = [], 0
            buf.append(ch)
            width += cw
        if buf:
            out.append("".join(buf).rstrip())
    return [ln for ln in out if ln]


def visible_text_lines(text: str) -> list[str]:
    """Printable lines only; pure formatting debris is not renderable text.

    The DXF codec already decoded text once, so re-running the full escape
    stripper here would damage literal backslashes. Only decode again when a
    known control marker is actually present (e.g. a user-typed string).
    """
    raw = str(text or "")
    if _FORMAT_MARKER.search(raw):
        raw = decode_cad_text(raw)
    out = []
    for line in raw.split("\n"):
        line = re.sub(r"^[;{}\s]+|[;{}\s]+$", "", line).strip()
        if line and re.search(r"[\u3400-\u9fff]|[A-Za-z]{2,}|\d", line):
            out.append(line)
    return out


def nurbs_points(control: Sequence[Point], knots: Sequence[float] | None = None,
                 degree: int = 3, weights: Sequence[float] | None = None,
                 per_span: int = 8, max_points: int = 400) -> list[Point]:
    """Evaluate a (possibly rational) B-spline into a display polyline.

    LibreDWG emits DXF SPLINE control points (10/20), a knot vector (40),
    degree (71) and optional weights (41).  Drawing the control polygon instead
    of the curve made every spline on a real sheet look like a scribble.  A
    clamped uniform knot vector is synthesised when the source omits / corrupts
    it, so a broken SPLINE still degrades to a sensible smooth curve.
    """
    ctrl = [(float(p[0]), float(p[1])) for p in control
            if len(p) >= 2 and math.isfinite(p[0]) and math.isfinite(p[1])]
    n = len(ctrl)
    if n < 2:
        return ctrl
    p = max(1, min(int(degree), n - 1))
    need = n + p + 1
    u = [float(v) for v in (knots or [])
         if math.isfinite(float(v))]
    if len(u) != need or any(u[i + 1] < u[i] - 1e-9 for i in range(len(u) - 1)):
        inner = need - 2 * (p + 1)
        u = ([0.0] * (p + 1) +
             [i / (inner + 1.0) for i in range(1, inner + 1)] +
             [1.0] * (p + 1))
    w = [float(x) if math.isfinite(float(x)) and float(x) > 0 else 1.0
         for x in (weights or [])]
    if len(w) != n:
        w = [1.0] * n
    hom = [(ctrl[i][0] * w[i], ctrl[i][1] * w[i], w[i]) for i in range(n)]

    def de_boor(t: float) -> Point | None:
        span = p
        for idx in range(p, n):
            if t < u[idx + 1]:
                span = idx
                break
        else:
            span = n - 1
        d = [hom[j + span - p] for j in range(p + 1)]
        for r in range(1, p + 1):
            for j in range(p, r - 1, -1):
                i = j + span - p
                den = u[i + p - r + 1] - u[i]
                alpha = 0.0 if abs(den) < EPS else (t - u[i]) / den
                a, b = d[j - 1], d[j]
                d[j] = ((1 - alpha) * a[0] + alpha * b[0],
                        (1 - alpha) * a[1] + alpha * b[1],
                        (1 - alpha) * a[2] + alpha * b[2])
        h = d[p]
        if abs(h[2]) < EPS:
            return None
        return (h[0] / h[2], h[1] / h[2])

    start, end = u[p], u[n]
    if end <= start + EPS:
        return ctrl
    spans: list[tuple[float, float]] = []
    cursor = start
    for idx in range(p, n):
        nxt = u[idx + 1]
        if nxt > cursor + EPS:
            spans.append((cursor, min(nxt, end)))
            cursor = nxt
        if cursor >= end - EPS:
            break
    out: list[Point] = []
    for lo, hi in spans:
        steps = max(2, min(per_span, int(max_points / max(1, len(spans)))))
        for k in range(steps):
            q = de_boor(lo + (hi - lo) * k / steps)
            if q and (not out or dist(out[-1], q) > 1e-9):
                out.append(q)
        if len(out) >= max_points:
            break
    last = de_boor(end)
    if last and (not out or dist(out[-1], last) > 1e-9):
        out.append(last)
    return out if len(out) >= 2 else ctrl


def hatch_pattern_lines(loops: Sequence[Sequence[Point]], spacing: float,
                        angle_deg: float = 45.0,
                        max_lines: int = 120) -> list[list[Point]]:
    """Clip parallel hatch strokes against boundary loops (even-odd rule).

    A HATCH boundary is *not* a solid filled region: painting it as one made
    real drawings look like large white blobs.  Intersections from all loops
    (outer boundary + holes) are sorted and paired, which reproduces the
    even-odd hole behaviour without any rasterisation.
    """
    flat = [tuple(p[:2]) for loop in loops for p in loop if len(p) >= 2]
    b = bounds_of(flat)
    if not b or spacing <= 0 or not math.isfinite(spacing):
        return []
    x0, y0, x1, y1 = b
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    a = math.radians(angle_deg)
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    ts = [nx * (x - cx) + ny * (y - cy) for x, y in corners]
    t0, t1 = min(ts), max(ts)
    if t1 - t0 < EPS:
        return []
    lines: list[list[Point]] = []
    t = math.ceil(t0 / spacing) * spacing
    while t <= t1 and len(lines) < max_lines:
        hits: list[float] = []
        for loop in loops:
            pts = [tuple(p[:2]) for p in loop if len(p) >= 2]
            if len(pts) < 2:
                continue
            for a_pt, b_pt in zip(pts, pts[1:] + pts[:1]):
                ex, ey = b_pt[0] - a_pt[0], b_pt[1] - a_pt[1]
                den = nx * ex + ny * ey
                if abs(den) < EPS:
                    continue
                s = (t - (nx * (a_pt[0] - cx) + ny * (a_pt[1] - cy))) / den
                if 0.0 <= s <= 1.0:
                    px = a_pt[0] + s * ex
                    py = a_pt[1] + s * ey
                    hits.append(dx * (px - cx) + dy * (py - cy))
        hits.sort()
        merged: list[float] = []
        for h in hits:
            if not merged or h - merged[-1] > 1e-7:
                merged.append(h)
        for i in range(0, len(merged) - 1, 2):
            s0, s1 = merged[i], merged[i + 1]
            if s1 - s0 <= 1e-7:
                continue
            lines.append([(cx + dx * s0 + nx * t, cy + dy * s0 + ny * t),
                          (cx + dx * s1 + nx * t, cy + dy * s1 + ny * t)])
            if len(lines) >= max_lines:
                break
        t += spacing
    return lines


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
