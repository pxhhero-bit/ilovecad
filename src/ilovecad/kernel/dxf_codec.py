# SPDX-License-Identifier: GPL-3.0-or-later
"""ASCII DXF codec used as the interchange format with GNU LibreDWG.

LibreDWG is the actual DWG read/write kernel; DXF is only the wire format
between the kernel and IloveCAD's in-memory 2D document model.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Iterable

from ..model import geometry as g
from ..model.document import DwgDocument, Layer
from ..model.entities import Entity
from ..version import SKIPPED_3D_TYPES, TARGET_VERSION

MAX_ENTITIES = 60000
CONTROL = {"SECTION", "ENDSEC", "TABLE", "ENDTAB", "EOF", "BLOCK", "ENDBLK",
           "SEQEND", "CLASS", "ENDSEC", "VERTEX"}
SUPPORTED = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE",
             "POINT", "TEXT", "MTEXT", "SOLID", "SPLINE", "HATCH",
             "DIMENSION", "INSERT"}

# Standard ACI linetype definitions, in drawing units. A positive segment is
# a dash, a negative one a gap; 49 codes repeat for the full pattern.
LTYPE_PATTERNS = {
    "CONTINUOUS": [],
    "DASHED": [0.5, -0.25],
    "HIDDEN": [0.25, -0.125],
    "CENTER": [0.75, -0.125, 0.125, -0.125],
    "PHANTOM": [0.75, -0.125, 0.125, -0.125, 0.125, -0.125],
    "DOT": [0.0, -0.125],
    "DASHDOT": [0.5, -0.125, 0.0, -0.125],
    "BORDER": [0.5, -0.125, 0.5, -0.125, 0.0, -0.125],
    "DIVIDE": [0.5, -0.125, 0.0, -0.125, 0.0, -0.125],
}


def normalize_newlines(data: bytes) -> bytes:
    """LibreDWG on Windows emits CR CR LF; normalise to plain LF."""
    return data.replace(b"\r\r\n", b"\n").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _decode(data: bytes) -> tuple[str, int]:
    """Decode DXF bytes, tolerating isolated broken multi-byte sequences.

    LibreDWG occasionally emits a truncated UTF-8 sequence inside a Chinese
    label.  A whole-file strict decode then fails, and an all-or-nothing
    fallback to cp1252/latin-1 turns *every* Chinese layer name and note into
    mojibake.  Decode line by line instead: DXF is a line-oriented format, so
    one bad byte can only damage its own line.  When most lines are valid
    UTF-8, damaged lines stay UTF-8 (with replacement chars); a genuinely
    GBK-encoded file is still decoded as GBK.
    """
    try:
        return data.decode("utf-8-sig"), 0
    except UnicodeDecodeError:
        pass

    raw_lines = data.split(b"\n")
    decoded: list[str | None] = [None] * len(raw_lines)
    utf8_ok = 0
    for idx, raw in enumerate(raw_lines):
        try:
            decoded[idx] = raw.decode("utf-8")
            utf8_ok += 1
        except UnicodeDecodeError:
            continue
    prefer_utf8 = utf8_ok >= max(1, int(len(raw_lines) * 0.6))

    repaired = 0
    for idx, raw in enumerate(raw_lines):
        if decoded[idx] is not None:
            continue
        if not prefer_utf8:
            try:
                decoded[idx] = raw.decode("gbk")
                continue
            except UnicodeDecodeError:
                pass
        line = raw.decode("utf-8", errors="replace")
        repaired += line.count("\ufffd")
        decoded[idx] = line
    text = "\n".join(decoded)  # type: ignore[arg-type]
    if text.startswith("\ufeff"):
        text = text[1:]
    return text, repaired


def _pairs(text: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    out = []
    for i in range(0, len(lines) - 1, 2):
        code = lines[i].strip()
        if not code:
            continue
        out.append((code, lines[i + 1].rstrip("\r")))
    return out


def _f(v, default: float = 0.0) -> float:
    try:
        x = float(str(v).strip())
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _first(groups, code: str, default=None):
    for c, v in groups:
        if c == code:
            return v
    return default


def _num(groups, code: str, default: float = 0.0) -> float:
    return _f(_first(groups, code, default), default)


def _common(groups) -> dict:
    lw = _num(groups, "370", 0.0)
    return {
        "layer": _first(groups, "8", "0") or "0",
        "color": int(_num(groups, "62", 256)),
        "linetype": _first(groups, "6", "BYLAYER") or "BYLAYER",
        "lineweight": lw / 100.0 if lw else 0.0,
    }


def _xy_pairs(groups, xcode="10", ycode="20"):
    pts = []
    for c, v in groups:
        if c == xcode:
            pts.append([_f(v), 0.0])
        elif c == ycode and pts:
            pts[-1][1] = _f(v)
    return [tuple(p) for p in pts]


def _parse_entity(kind: str, groups) -> Entity | None:
    c = _common(groups)
    d: dict = {}
    if kind == "LINE":
        d = {"a": (_num(groups, "10"), _num(groups, "20")),
             "b": (_num(groups, "11"), _num(groups, "21"))}
    elif kind == "LWPOLYLINE":
        pts = []
        for code, v in groups:
            if code == "10":
                pts.append([_f(v), 0.0, 0.0])
            elif code == "20" and pts:
                pts[-1][1] = _f(v)
            elif code == "42" and pts:
                pts[-1][2] = _f(v)
        d = {"pts": [tuple(p) for p in pts],
             "closed": bool(int(_num(groups, "70")) & 1)}
        if len(pts) < 2:
            return None
    elif kind == "CIRCLE":
        d = {"c": (_num(groups, "10"), _num(groups, "20")),
             "r": abs(_num(groups, "40"))}
        if d["r"] <= 0:
            return None
    elif kind == "ARC":
        d = {"c": (_num(groups, "10"), _num(groups, "20")),
             "r": abs(_num(groups, "40")),
             "start": _num(groups, "50"), "end": _num(groups, "51")}
        if d["r"] <= 0:
            return None
    elif kind == "ELLIPSE":
        d = {"c": (_num(groups, "10"), _num(groups, "20")),
             "major": (_num(groups, "11"), _num(groups, "21")),
             "ratio": max(abs(_num(groups, "40", 1.0)), 1e-6),
             "start": _num(groups, "41"), "end": _num(groups, "42", 2 * math.pi)}
    elif kind == "POINT":
        d = {"p": (_num(groups, "10"), _num(groups, "20"))}
    elif kind in ("TEXT", "MTEXT", "DIMENSION"):
        is_mtext = kind == "MTEXT"
        is_dim = kind == "DIMENSION"
        if is_mtext:
            raw_text = "".join(v for code, v in groups if code in ("3", "1"))
        else:
            raw_text = _first(groups, "1", "") or ""
        fmt = g.parse_mtext_format(raw_text) if is_mtext else {}
        style = _first(groups, "7", "Standard") or "Standard"
        align = 1
        baseline = False          # TEXT with group 73=0 sits on the baseline
        raw_height = abs(_num(groups, "40", 0.0))
        height = raw_height if raw_height > 1e-6 else 0.0
        if fmt.get("absolute_height", 0.0) > 0:
            height = fmt["absolute_height"]
        if is_dim:
            # Dimension lines / arrows live in an anonymous block (*D…); the
            # DIMENSION entity itself carries only a placeholder ("<>"). Never
            # render that placeholder as literal text.
            p = (_num(groups, "11"), _num(groups, "21"))
            if not (p[0] or p[1]):
                p = (_num(groups, "10"), _num(groups, "20"))
            raw_text = ""
        elif is_mtext:
            # MTEXT group 11/21 is the X-axis direction vector, NOT a point.
            p = (_num(groups, "10"), _num(groups, "20"))
            a71 = int(_num(groups, "71", 0))
            a72 = int(_num(groups, "72", 0))
            g40 = abs(_num(groups, "40", 0.0))
            g44 = _num(groups, "44", 1.0)
            legacy = ((g44 > 8 and (abs(g40 - g44) < 1e-6 or g40 > 1000))
                      or (a71 == 0 and 1 <= a72 <= 9))
            # Standard DXF: 71 = attachment (1..9), 72 = drawing direction.
            # LibreDWG's legacy writer shifts the attachment into 72 and
            # writes 0 / 2 into 71, which pushed top-left labels to the wrong
            # side of their title-block cell.
            if legacy and 1 <= a72 <= 9:
                align = a72
            else:
                align = a71 if 1 <= a71 <= 9 else 1
        else:
            # TEXT group 11/21 is the alignment point when 72/73 are non-zero.
            hjust = int(_num(groups, "72", 0))
            vjust = int(_num(groups, "73", 0))
            raw_p = (_num(groups, "10"), _num(groups, "20"))
            ap = (_num(groups, "11"), _num(groups, "21"))
            # dwg2dxf drops TEXT group 73 (vertical justification).  The
            # source alignment point is then the only evidence: when it sits
            # about half a text height ABOVE the insertion point, the label is
            # middle-justified and 11/21 is its visual CENTRE.  Title-block
            # cells are built exactly that way; treating 11/21 as the baseline
            # shifted every label up by ~0.3 * height and onto the grid line.
            if (vjust == 0 and hjust in (1, 2, 4, 5) and height > 0
                    and (ap[0] or ap[1])
                    and 0.25 * height <= ap[1] - raw_p[1] <= 0.85 * height):
                vjust = 2
            baseline = vjust == 0
            hpos = {0: 0, 1: 1, 2: 2, 3: 0, 4: 1, 5: 1}.get(hjust, 0)
            vpos = {0: 2, 1: 2, 2: 1, 3: 0}.get(vjust, 2)
            align = vpos * 3 + hpos + 1
            p = raw_p
            if (hjust or vjust) and (ap[0] or ap[1]):
                p = ap
        if is_mtext:
            align = max(1, min(9, align))
        # Standard DXF: MTEXT group 41 is the reference rectangle width in
        # DRAWING UNITS.  Treating it as a character count (as the legacy
        # LibreDWG dialect suggested) wraps nothing or wraps far too early.
        # TEXT group 41 is a relative width factor instead.
        g41 = _num(groups, "41", 0.0)
        width_factor = g41 if (not is_mtext and g41 > 0) else 0.0
        column_chars = (g41 / (height * 0.6)
                        if is_mtext and g41 > 0 and height > 0 else 0.0)
        # Group 44 is a RELATIVE line-spacing factor (normally 1.0).  Values
        # above 8 come from the legacy converter repeating the rectangle width.
        g44 = _num(groups, "44", 1.0)
        line_spacing = 1.0 if g44 > 8 else g44
        line_spacing = max(0.5, min(3.0, line_spacing or 1.0))
        d = {"p": p,
             "text": g.decode_cad_text(raw_text),
             "height": height,
             "rotation": _num(groups, "50"),
             "style": style,
             "width_factor": width_factor,
             "width_scale": fmt.get("width_scale", 1.0),
             "height_scale": fmt.get("height_scale", 1.0),
             "oblique": fmt.get("oblique", 0.0) or _num(groups, "51", 0.0),
             "color_index": fmt.get("color_index"),
             "inline_font": fmt.get("inline_font", ""),
             "bold": fmt.get("bold", False), "italic": fmt.get("italic", False),
             "underline": fmt.get("underline", False),
             "strikeout": fmt.get("strikeout", False),
             "column_chars": column_chars,
             "line_spacing": line_spacing,
             "align": align, "baseline": baseline,
             "mtext": is_mtext, "dimension": is_dim,
             "source_kind": kind}
        if fmt.get("align") is not None and is_mtext:
            # \A1 bottom / \A2 centre / \A3 top overrides the vertical part.
            vert = {1: 2, 2: 1, 3: 0}.get(fmt["align"], 1)
            d["align"] = vert * 3 + ((align - 1) % 3) + 1
        kind = {"MTEXT": "TEXT", "DIMENSION": "DIMENSION"}.get(kind, kind)
    elif kind == "SOLID":
        pts = [(_num(groups, "10"), _num(groups, "20")),
               (_num(groups, "11"), _num(groups, "21")),
               (_num(groups, "12"), _num(groups, "22")),
               (_num(groups, "13"), _num(groups, "23"))]
        d = {"pts": pts}
    elif kind == "SPLINE":
        # DXF SPLINE: 10/20 control points, 11/21 fit points (rarely present),
        # 40 knot vector, 41 weights, 71 degree, 70 flags. Evaluating the real
        # B-spline instead of drawing its control polygon is essential: a
        # control polygon is a hull, not the curve, and reads as scribble.
        ctrl = _xy_pairs(groups, "10", "20") or _xy_pairs(groups, "11", "21")
        if len(ctrl) < 2:
            return None
        knots = [_f(v) for code, v in groups if code == "40"]
        weights = [_f(v, 1.0) for code, v in groups if code == "41"]
        if len(weights) != len(ctrl):
            weights = []
        degree = int(_num(groups, "71", 3)) or 3
        flags = int(_num(groups, "70"))
        pts = g.nurbs_points(ctrl, knots, degree, weights or None)
        d = {"pts": pts, "control": ctrl, "knots": knots,
             "degree": degree, "weights": weights,
             "closed": bool(flags & 1)}
    elif kind == "HATCH":
        # A HATCH starts with an elevation point (10/20/30, usually 0,0) and
        # an extrusion vector, THEN one or more boundary loops announced by
        # group 93 (vertex count). Naively collecting every 10/20 pair mixed
        # the elevation point into the loop — drawing a huge fake diagonal
        # across the sheet — and merged separate loops into one polygon.
        vertices: list[tuple[float, float, float]] = []
        sizes: list[int] = []
        i = 0
        elevation_skipped = False
        while i < len(groups):
            gc, gv = groups[i]
            if gc == "93":
                sizes.append(max(0, int(_f(gv))))
                i += 1
                continue
            if gc != "10":
                i += 1
                continue
            x, y, bulge = _f(gv), 0.0, 0.0
            j = i + 1
            while j < len(groups) and groups[j][0] in ("20", "42"):
                if groups[j][0] == "20":
                    y = _f(groups[j][1])
                else:
                    bulge = _f(groups[j][1])
                j += 1
            following = {groups[k][0]
                         for k in range(j, min(j + 3, len(groups)))}
            if not elevation_skipped and following & {"30", "210"}:
                elevation_skipped = True   # the header elevation point
                i = j
                continue
            vertices.append((x, y, bulge))
            i = j
        if not vertices:
            vertices = [(p[0], p[1], 0.0) for p in
                        _xy_pairs(groups, "11", "21")]
        paths: list[list[tuple[float, float, float]]] = []
        if sizes and sum(sizes) <= len(vertices):
            pos = 0
            for n in sizes:
                seg = vertices[pos:pos + n]
                pos += n
                if len(seg) >= 2:
                    paths.append(seg)
            if pos < len(vertices):
                paths.append(vertices[pos:])
        elif len(vertices) >= 3:
            paths = [vertices]     # minimal writers omit group 93
        d = {"paths": paths}
        if not d["paths"]:
            return None
    elif kind == "INSERT":
        d = {"name": _first(groups, "2", "") or "",
             "p": (_num(groups, "10"), _num(groups, "20")),
             "sx": _num(groups, "41", 1.0) or 1.0,
             "sy": _num(groups, "42", 1.0) or 1.0,
             "rotation": _num(groups, "50")}
    else:
        return None
    handle = _first(groups, "5", "") or ""
    try:
        d["handle"] = int(str(handle).strip(), 16)
    except ValueError:
        d["handle"] = 0
    return Entity(0, kind, c["layer"], c["color"], c["linetype"],
                  c["lineweight"], d)


def parse_dxf(data: bytes) -> tuple[DwgDocument, dict]:
    """Parse a DXF byte stream into a 2D document plus an import report dict."""
    text, repaired = _decode(normalize_newlines(data))
    pairs = _pairs(text)
    doc = DwgDocument()
    blocks: dict[str, list[Entity]] = defaultdict(list)
    block_base: dict[str, tuple[float, float]] = {}
    header: dict[str, list[tuple[str, str]]] = {}
    report: dict = {"skipped": defaultdict(int), "notes": [],
                    "warnings": [], "block_expanded": 0}
    if repaired:
        report["warnings"].append(
            f"DXF 中有 {repaired} 处损坏的文本字节已按 UTF-8 容错替换；"
            "仅影响对应字符，不影响图纸几何")

    section = None
    table = None
    block_name = None
    pending_poly: Entity | None = None
    top_inserts: list[Entity] = []

    def add_entity(ent: Entity | None, in_block: bool):
        nonlocal block_name, pending_poly
        if ent is None:
            return
        if in_block and block_name:
            blocks[block_name].append(ent)
        else:
            doc.entities.append(ent)

    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0":
            j = i + 1
            groups = []
            while j < len(pairs) and pairs[j][0] not in ("0", "9"):
                groups.append(pairs[j])
                j += 1
            typ = value.strip().upper()
            if typ == "SECTION":
                section = (_first(groups, "2", "") or "").upper()
                table = None
            elif typ == "ENDSEC":
                section, table = None, None
            elif typ == "TABLE":
                table = (_first(groups, "2", "") or "").upper()
            elif typ == "ENDTAB":
                table = None
            elif section == "TABLES" and table == "LAYER" and typ == "LAYER":
                name = _first(groups, "2", "0") or "0"
                flags = int(_num(groups, "70"))
                raw_color = int(_num(groups, "62", 7))
                doc.layers[name] = Layer(
                    name=name, color=abs(raw_color) or 7,
                    linetype=_first(groups, "6", "CONTINUOUS") or "CONTINUOUS",
                    lineweight=_num(groups, "370", 25.0) / 100.0,
                    visible=raw_color >= 0 and not (flags & 1),
                    frozen=bool(flags & 1), locked=bool(flags & 4))
            elif section == "TABLES" and table == "STYLE" and typ == "STYLE":
                # group 3 primary font, group 4 CJK bigfont, 40 fixed height,
                # 41 width factor, 42 last height, 50 oblique angle.
                name = _first(groups, "2", "Standard") or "Standard"
                doc.styles[name] = {
                    "font": _first(groups, "3", "") or "",
                    "bigfont": _first(groups, "4", "") or "",
                    "fixed_height": _num(groups, "40", 0.0),
                    "width": _num(groups, "41", 1.0) or 1.0,
                    "last_height": _num(groups, "42", 2.5),
                    "oblique": _num(groups, "50", 0.0),
                }
            elif section == "BLOCKS" and typ == "BLOCK":
                block_name = _first(groups, "2", "") or ""
                block_base[block_name] = (_num(groups, "10"), _num(groups, "20"))
            elif section == "BLOCKS" and typ == "ENDBLK":
                block_name = None
            elif typ == "POLYLINE":
                ent = Entity(0, "LWPOLYLINE", **_common(groups),
                             data={"pts": [], "closed": bool(int(_num(groups, "70")) & 1)})
                pending_poly = ent
            elif typ == "VERTEX" and pending_poly is not None:
                pending_poly.data["pts"].append(
                    (_num(groups, "10"), _num(groups, "20"), _num(groups, "42")))
            elif typ == "SEQEND":
                if pending_poly is not None:
                    if len(pending_poly.data["pts"]) >= 2:
                        add_entity(pending_poly, section == "BLOCKS")
                    pending_poly = None
            elif typ == "EOF":
                pass
            elif section in ("ENTITIES", "BLOCKS"):
                if typ in SKIPPED_3D_TYPES:
                    report["skipped"]["3D:" + typ] += 1
                elif typ in SUPPORTED or typ == "INSERT":
                    ent = _parse_entity(typ, groups)
                    if typ == "INSERT" and ent is not None and section == "ENTITIES":
                        top_inserts.append(ent)
                    else:
                        add_entity(ent, section == "BLOCKS")
                elif typ in CONTROL:
                    pass
                else:
                    report["skipped"][typ] += 1
            i = j
        elif code == "9" and value.startswith("$"):
            j = i + 1
            vals = []
            while j < len(pairs) and pairs[j][0] not in ("0", "9"):
                vals.append(pairs[j])
                j += 1
            header[value.strip().upper()] = vals
            i = j
        else:
            i += 1

    if pending_poly is not None and len(pending_poly.data["pts"]) >= 2:
        doc.entities.append(pending_poly)

    doc.units = int(_f(_first(header.get("$INSUNITS", []), "70", 4), 4))
    # Keep the source's own unit marker: the UI may override doc.units with a
    # local display preference, but saving must not silently relabel a drawing.
    doc.source_units = doc.units
    if doc.units == 0:
        report["warnings"].append(
            "源图纸 $INSUNITS=0（无单位）；测量数值按本机显示单位解释，"
            "保存时保留源单位标记，请自行确认图纸实际单位")
    doc.source_version = (_first(header.get("$ACADVER", []), "1", "") or "").strip().upper()

    def expand(name: str, p, sx: float, sy: float, rot: float, depth: int = 0):
        if depth > 3 or len(doc.entities) > MAX_ENTITIES or name not in blocks:
            return
        base = block_base.get(name, (0.0, 0.0))
        bp = g.scale_point(base, (0.0, 0.0), sx, sy)
        bp = g.rotate(bp, (0.0, 0.0), rot)
        dx, dy = p[0] - bp[0], p[1] - bp[1]
        for src in blocks[name]:
            if len(doc.entities) > MAX_ENTITIES:
                report["notes"].append("块展开达到实体上限，剩余部分已跳过")
                break
            if src.kind == "INSERT":
                q = g.scale_point(src.data["p"], (0.0, 0.0), sx, sy)
                q = g.rotate(q, (0.0, 0.0), rot)
                expand(src.data["name"], (q[0] + dx, q[1] + dy),
                       sx * src.data.get("sx", 1.0), sy * src.data.get("sy", 1.0),
                       rot + src.data.get("rotation", 0.0), depth + 1)
                continue
            e = src.clone()
            e.eid = 0
            e.transform("scale", (0.0, 0.0), sx, sy)
            e.transform("rotate", (0.0, 0.0), rot)
            e.transform("translate", dx, dy)
            doc.add(e)
            report["block_expanded"] += 1

    for ins in top_inserts:
        d = ins.data
        expand(d["name"], d["p"], d.get("sx", 1.0), d.get("sy", 1.0),
               d.get("rotation", 0.0))
    if top_inserts:
        report["notes"].append(
            f"顶层块参照 {len(top_inserts)} 个已展开为 {report['block_expanded']} 个 2D 实体")

    # Resolve every text entity's STYLE reference.  A zero group-40 height is
    # common in LibreDWG output (dimension text, attribute placeholders); the
    # style's fixed / last-used height is the only faithful fallback.  Group 41
    # of a TEXT is a real width factor; when absent, inherit the style's width.
    style_lookup = {k.lower(): v for k, v in doc.styles.items()}
    for e in doc.entities:
        if e.kind not in ("TEXT", "MTEXT", "DIMENSION"):
            continue
        d = e.data
        st = style_lookup.get(str(d.get("style", "Standard")).lower(), {})
        d["font"] = st.get("font", "")
        d["bigfont"] = st.get("bigfont", "")
        d["style_width"] = st.get("width", 1.0) or 1.0
        d["style_oblique"] = st.get("oblique", 0.0)
        if d.get("height", 0.0) <= 0:
            d["height"] = (st.get("fixed_height", 0.0) or
                           st.get("last_height", 0.0) or 2.5)
        if not d.get("width_factor"):
            d["width_factor"] = d["style_width"]
        if not d.get("oblique"):
            d["oblique"] = d.get("style_oblique", 0.0)
    if doc.styles:
        report["styles"] = len(doc.styles)
        report["notes"].append(
            f"已解析 {len(doc.styles)} 个文字样式；SHX 字型按最接近的本机"
            "中文字体近似显示（黑体 / 宋体 / 楷体 / 仿宋 / ISO 单线体）")

    # Snap labels to the centres of the title-block cells.  This is a viewer
    # transform only: it fixes LibreDWG's lost group 73 / font-metric mismatch
    # without touching the source bytes or anything written back.
    from ..model.titleblock import apply_title_block_alignment
    aligned = apply_title_block_alignment(doc, doc.align_title_text)
    if aligned:
        report["title_aligned"] = aligned
        report["notes"].append(
            f"已按图纸自身的标题栏格线对 {aligned} 个文字做显示层居中校正"
            "（仅影响看图，源文件与写出数据不变；可用 TITLEALIGN 关闭）")

    for idx, e in enumerate(doc.entities, start=1):
        e.eid = idx
    doc._next_id = len(doc.entities) + 1
    for name, layer in list(doc.layers.items()):
        if layer.color == 0:
            layer.color = 7
    if "0" not in doc.layers:
        doc.layers["0"] = Layer("0")

    report["header_vars"] = len(header)
    report["layers"] = len(doc.layers)
    report["entities"] = len(doc.entities)
    report["by_kind"] = dict(doc.entity_count_by_kind())
    if report["by_kind"].get("SPLINE"):
        report["notes"].append(
            f"SPLINE × {report['by_kind']['SPLINE']} 已按 NURBS 曲线求值后以折线显示 / 写出，"
            "写回仍为 2D 折线近似（非原生样条），测量时请勿以折线代替原曲线")
    if report["by_kind"].get("HATCH"):
        report["notes"].append(
            f"HATCH × {report['by_kind']['HATCH']} 按边界多段线写出；屏幕 / PDF "
            "显示边界轮廓与近似斜线图案，原填充图案不会还原")
    report["skipped"] = dict(report["skipped"])
    doc.skipped.update(report["skipped"])
    doc.notes.extend(report["notes"])
    return doc, report


def _dxf_text(s: str) -> str:
    out = []
    for ch in str(s):
        if ord(ch) > 126:
            out.append(f"\\U+{ord(ch):04X}")
        elif ch in "\r\n":
            out.append("\\P")
        else:
            out.append(ch)
    return "".join(out)


class _Writer:
    def __init__(self):
        self.lines: list[str] = []

    def p(self, code, value):
        self.lines.append(f"{code}\n{value}\n")

    def xyz(self, x, y, xcode=10, ycode=20):
        self.p(xcode, f"{x:.6f}")
        self.p(ycode, f"{y:.6f}")
        self.p(xcode + 20, 0)

    def common(self, e: Entity):
        self.p(8, e.layer)
        if e.color != 256:
            self.p(62, e.color)
        if e.linetype and e.linetype.upper() != "BYLAYER":
            self.p(6, e.linetype)
        if e.lineweight:
            self.p(370, int(round(e.lineweight * 100)))

    def text(self) -> bytes:
        return "".join(self.lines).encode("utf-8")


def write_dxf(doc: DwgDocument, stats: dict | None = None) -> bytes:
    """Serialise the 2D model into a structurally complete AC1018 ASCII DXF.

    LibreDWG's DXF importer requires a BLOCK_RECORD table, unique handles and
    owner (330) references. Without them dwgwrite reports
    "dwg.block_control ... missing" and silently drops every entity. The
    skeleton below mirrors a dwgread -O DXF export and round-trips R2004.

    ``stats`` (optional) receives an honest account of the conversion: the
    exact number of DXF entities written, which source types were approximated
    by another DXF type, and which entities were skipped.  The writer must
    never claim a successful round-trip while silently dropping geometry.
    """
    if stats is not None:
        stats.clear()
        stats.update({"source": len(doc.entities), "written": 0,
                      "converted": Counter(), "skipped": Counter(),
                      "dropped": 0, "point_fallback": 0})
    w = _Writer()
    # Fixed low handles for tables/records; entity handles start at 0x1000 so
    # they can never collide with table records no matter how many layers exist.
    next_handle = 0x1000

    def H() -> str:
        nonlocal next_handle
        h = format(next_handle, "X")
        next_handle += 1
        return h

    MODEL_BR, PAPER_BR = "10", "11"  # BLOCK_RECORD handles

    w.p(999, "IloveCAD 2D / GPL-3.0-or-later")
    w.p(0, "SECTION"); w.p(2, "HEADER")
    w.p(9, "$ACADVER"); w.p(1, TARGET_VERSION)
    # A locally chosen display unit must never relabel the source drawing.
    source_units = doc.source_units if doc.source_units is not None else doc.units
    w.p(9, "$INSUNITS"); w.p(70, source_units)
    w.p(9, "$MEASUREMENT"); w.p(70, 1)
    w.p(9, "$HANDSEED"); w.p(5, "__HANDSEED__")
    b = doc.full_bounds() or (0, 0, 100, 100)
    w.p(9, "$EXTMIN"); w.p(10, f"{b[0]:.6f}"); w.p(20, f"{b[1]:.6f}"); w.p(30, 0)
    w.p(9, "$EXTMAX"); w.p(10, f"{b[2]:.6f}"); w.p(20, f"{b[3]:.6f}"); w.p(30, 0)
    w.p(0, "ENDSEC")

    w.p(0, "SECTION"); w.p(2, "TABLES")
    # Every linetype referenced by a layer or entity needs a table record,
    # otherwise LibreDWG's DXF importer drops the reference on write-back.
    linetypes = {"CONTINUOUS"}
    for layer in doc.layers.values():
        name = (layer.linetype or "CONTINUOUS").upper()
        if name and name != "BYLAYER":
            linetypes.add(name)
    for ent in doc.entities:
        name = (ent.linetype or "").upper()
        if name and name != "BYLAYER":
            linetypes.add(name)
    linetypes = {name for name in linetypes if name in LTYPE_PATTERNS}
    w.p(0, "TABLE"); w.p(2, "LTYPE"); w.p(5, "3"); w.p(330, "0")
    w.p(100, "AcDbSymbolTable"); w.p(70, len(linetypes))
    for i, name in enumerate(sorted(linetypes)):
        pattern = LTYPE_PATTERNS[name]
        w.p(0, "LTYPE"); w.p(5, format(0x30 + i, "X")); w.p(330, "3")
        w.p(100, "AcDbSymbolTableRecord"); w.p(100, "AcDbLinetypeTableRecord")
        w.p(2, name); w.p(70, 0); w.p(3, f"IloveCAD {name}")
        w.p(72, 65); w.p(73, len(pattern))
        w.p(40, sum(abs(seg) for seg in pattern))
        for seg in pattern:
            w.p(49, f"{seg:.6f}")
    w.p(0, "ENDTAB")

    # Text styles carry the SHX / TrueType font names, width factor and
    # fallback height.  Writing them back keeps font identity on a round trip
    # instead of silently degrading every label to the same system font.
    styles = doc.styles or {"Standard": {
        "font": "txt.shx", "bigfont": "", "fixed_height": 0.0,
        "width": 1.0, "last_height": 2.5, "oblique": 0.0}}
    w.p(0, "TABLE"); w.p(2, "STYLE"); w.p(5, "4"); w.p(330, "0")
    w.p(100, "AcDbSymbolTable"); w.p(70, len(styles))
    for name, st in styles.items():
        w.p(0, "STYLE"); w.p(5, H()); w.p(330, "4")
        w.p(100, "AcDbSymbolTableRecord")
        w.p(100, "AcDbTextStyleTableRecord")
        w.p(2, name); w.p(70, 0)
        w.p(40, f"{float(st.get('fixed_height', 0.0)):.6f}")
        w.p(41, f"{float(st.get('width', 1.0) or 1.0):.6f}")
        w.p(42, f"{float(st.get('last_height', 2.5)):.6f}")
        w.p(50, f"{float(st.get('oblique', 0.0)):.6f}")
        w.p(3, str(st.get("font", "") or "txt.shx"))
        w.p(4, str(st.get("bigfont", "") or ""))
        w.p(71, 0)
    w.p(0, "ENDTAB")

    layers = sorted(doc.layers.values(), key=lambda l: l.name)
    w.p(0, "TABLE"); w.p(2, "LAYER"); w.p(5, "2"); w.p(330, "0")
    w.p(100, "AcDbSymbolTable"); w.p(70, len(layers))
    for layer in layers:
        w.p(0, "LAYER"); w.p(5, H()); w.p(330, "2")
        w.p(100, "AcDbSymbolTableRecord"); w.p(100, "AcDbLayerTableRecord")
        w.p(2, layer.name)
        w.p(70, (1 if layer.frozen else 0) + (4 if layer.locked else 0))
        w.p(62, layer.color if layer.visible else -layer.color)
        w.p(6, layer.linetype or "CONTINUOUS")
        w.p(370, int(round((layer.lineweight or 0.25) * 100)))
        w.p(390, "0")
    w.p(0, "ENDTAB")

    w.p(0, "TABLE"); w.p(2, "BLOCK_RECORD"); w.p(5, "1"); w.p(330, "0")
    w.p(100, "AcDbSymbolTable"); w.p(70, 2)
    for br, name in ((MODEL_BR, "*Model_Space"), (PAPER_BR, "*Paper_Space")):
        w.p(0, "BLOCK_RECORD"); w.p(5, br); w.p(330, "1")
        w.p(100, "AcDbSymbolTableRecord"); w.p(100, "AcDbBlockTableRecord")
        w.p(2, name); w.p(340, "0")
    w.p(0, "ENDTAB")
    w.p(0, "ENDSEC")

    w.p(0, "SECTION"); w.p(2, "BLOCKS")
    for br, name in ((MODEL_BR, "*Model_Space"), (PAPER_BR, "*Paper_Space")):
        w.p(0, "BLOCK"); w.p(5, H()); w.p(330, br)
        w.p(100, "AcDbEntity"); w.p(8, "0"); w.p(100, "AcDbBlockBegin")
        w.p(2, name); w.p(70, 0)
        w.p(10, 0); w.p(20, 0); w.p(30, 0); w.p(3, name); w.p(1, "")
        w.p(0, "ENDBLK"); w.p(5, H()); w.p(330, br)
        w.p(100, "AcDbEntity"); w.p(8, "0"); w.p(100, "AcDbBlockEnd")
    w.p(0, "ENDSEC")

    subclasses = {
        "LINE": "AcDbLine", "LWPOLYLINE": "AcDbPolyline",
        "CIRCLE": "AcDbCircle", "ARC": "AcDbCircle",
        "ELLIPSE": "AcDbEllipse", "POINT": "AcDbPoint",
        "TEXT": "AcDbText", "SOLID": "AcDbTrace",
    }

    def begin(kind: str, e: Entity, subclass: str | None = None) -> None:
        w.p(0, kind); w.p(5, H()); w.p(330, MODEL_BR)
        w.p(100, "AcDbEntity")
        w.common(e)
        w.p(100, subclass or subclasses[kind])
        if stats is not None:
            stats["written"] += 1

    def converted(source_kind: str, target_kind: str) -> None:
        if stats is not None and source_kind != target_kind:
            stats["converted"][f"{source_kind}→{target_kind}"] += 1

    def dropped() -> None:
        if stats is not None:
            stats["dropped"] += 1

    w.p(0, "SECTION"); w.p(2, "ENTITIES")
    for e in doc.entities:
        d = e.data
        k = e.kind
        if k == "LINE":
            begin("LINE", e)
            w.xyz(*d["a"]); w.xyz(*d["b"], xcode=11, ycode=21)
        elif k in ("LWPOLYLINE", "POLYLINE", "SPLINE", "HATCH"):
            # HATCH may carry several boundary loops; every loop becomes its
            # own closed polyline instead of silently dropping all but one.
            if k == "HATCH" and isinstance(d.get("paths"), list):
                sequences = [list(seq) for seq in d["paths"] if seq]
            else:
                pts = d.get("pts") or []
                sequences = [list(pts)] if pts else []
            for seq in sequences:
                points = [tuple(p) for p in seq
                          if isinstance(p, (tuple, list)) and len(p) >= 2]
                if len(points) == 1:
                    # A degenerate HATCH loop still carries a real position;
                    # keep it as a POINT rather than silently dropping data.
                    begin("POINT", e)
                    w.xyz(points[0][0], points[0][1])
                    if stats is not None:
                        stats["point_fallback"] += 1
                    continue
                if len(points) < 2:
                    dropped()
                    continue
                begin("LWPOLYLINE", e)
                w.p(90, len(points))
                w.p(70, 1 if (d.get("closed") or k == "HATCH") else 0)
                for pt in points:
                    w.p(10, f"{pt[0]:.6f}"); w.p(20, f"{pt[1]:.6f}")
                    # Keep the vertex bulge: dropping it turns arc segments of
                    # a polyline into straight lines on every save.
                    bulge = pt[2] if len(pt) > 2 else 0.0
                    if bulge:
                        w.p(42, f"{bulge:.6f}")
            converted(k, "LWPOLYLINE")
        elif k == "CIRCLE":
            begin("CIRCLE", e); w.xyz(*d["c"]); w.p(40, f"{d['r']:.6f}")
        elif k == "ARC":
            begin("ARC", e); w.xyz(*d["c"]); w.p(40, f"{d['r']:.6f}")
            w.p(100, "AcDbArc")
            w.p(50, f"{d['start']:.6f}"); w.p(51, f"{d['end']:.6f}")
        elif k == "ELLIPSE":
            begin("ELLIPSE", e); w.xyz(*d["c"])
            w.xyz(*d["major"], xcode=11, ycode=21)
            w.p(40, f"{d['ratio']:.6f}"); w.p(41, f"{d['start']:.6f}")
            w.p(42, f"{d['end']:.6f}")
        elif k == "POINT":
            begin("POINT", e); w.xyz(*d["p"])
        elif k in ("TEXT", "MTEXT", "DIMENSION"):
            # A TEXT entity cannot carry MTEXT's \P line breaks.  Writing a
            # whole multi-line note as one TEXT produced a literal "\P" in the
            # saved drawing.  Emit one TEXT per line instead, preserving the
            # source height, style, rotation, width and line spacing.
            lines = g.visible_text_lines(str(d.get("text", ""))) or [""]
            h = float(d.get("height", 2.5) or 2.5)
            spacing = max(0.5, min(3.0,
                float(d.get("line_spacing", 1.0) or 1.0)))
            step = h * 1.25 * spacing
            align = max(1, min(9, int(d.get("align", 1) or 1)))
            vpos = (align - 1) // 3
            p = tuple(d.get("p", (0.0, 0.0)))
            n = len(lines)
            for i, line in enumerate(lines):
                if vpos == 0:          # top attachment: lines go downwards
                    y = p[1] - i * step
                elif vpos == 1:        # middle
                    y = p[1] + ((n - 1) / 2.0 - i) * step
                else:                  # bottom / baseline: upwards
                    y = p[1] + i * step
                begin("TEXT", e)
                w.xyz(p[0], y)
                # Always write the SOURCE height: viewer-side display scaling
                # must never leak into a saved drawing.
                w.p(40, f"{h:.6f}")
                w.p(1, _dxf_text(line))
                w.p(50, f"{d.get('rotation', 0.0):.6f}")
                w.p(41, f"{float(d.get('width_factor', 1.0) or 1.0):.6f}")
                if d.get("oblique"):
                    w.p(51, f"{float(d['oblique']):.6f}")
                w.p(7, str(d.get("style", "Standard") or "Standard"))
            converted(k, "TEXT")
            if stats is not None and n > 1:
                stats["mtext_lines"] = stats.get("mtext_lines", 0) + n
        elif k == "SOLID":
            pts = list(d.get("pts", []))
            if len(pts) < 3:
                dropped()
                continue
            begin("SOLID", e)
            for idx, pt in enumerate(pts[:4]):
                w.xyz(pt[0], pt[1], xcode=10 + idx, ycode=20 + idx)
        else:
            # Never silently discard an entity type we do not know how to
            # serialise; the caller reports it and refuses the write if needed.
            if stats is not None:
                stats["skipped"][k or "UNKNOWN"] += 1
    w.p(0, "ENDSEC")
    w.p(0, "EOF")
    return "".join(w.lines).replace("__HANDSEED__", format(next_handle, "X")).encode("utf-8")
