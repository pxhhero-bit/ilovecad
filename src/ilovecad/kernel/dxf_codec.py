# SPDX-License-Identifier: GPL-3.0-or-later
"""ASCII DXF codec used as the interchange format with GNU LibreDWG.

LibreDWG is the actual DWG read/write kernel; DXF is only the wire format
between the kernel and IloveCAD's in-memory 2D document model.
"""
from __future__ import annotations

import math
from collections import defaultdict
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


def normalize_newlines(data: bytes) -> bytes:
    """LibreDWG on Windows emits CR CR LF; normalise to plain LF."""
    return data.replace(b"\r\r\n", b"\n").replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "gbk", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


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
        if kind == "MTEXT":
            # MTEXT splits long strings over repeated group 3 then a final group 1.
            text = "".join(v for code, v in groups if code in ("3", "1"))
        else:
            text = _first(groups, "1", "") or ""
        d = {"p": (_num(groups, "10"), _num(groups, "20")),
             "text": g.decode_cad_text(text),
             "height": abs(_num(groups, "40", 2.5)) or 2.5,
             "rotation": _num(groups, "50"),
             "width": _num(groups, "41")}
        kind = {"MTEXT": "TEXT", "DIMENSION": "DIMENSION"}.get(kind, kind)
    elif kind == "SOLID":
        pts = [(_num(groups, "10"), _num(groups, "20")),
               (_num(groups, "11"), _num(groups, "21")),
               (_num(groups, "12"), _num(groups, "22")),
               (_num(groups, "13"), _num(groups, "23"))]
        d = {"pts": pts}
    elif kind == "SPLINE":
        pts = _xy_pairs(groups, "10", "20") or _xy_pairs(groups, "11", "21")
        if len(pts) < 2:
            return None
        d = {"pts": pts}
    elif kind == "HATCH":
        pts = _xy_pairs(groups, "10", "20")
        d = {"paths": [pts] if len(pts) > 2 else []}
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
    return Entity(0, kind, c["layer"], c["color"], c["linetype"],
                  c["lineweight"], d)


def parse_dxf(data: bytes) -> tuple[DwgDocument, dict]:
    """Parse a DXF byte stream into a 2D document plus an import report dict."""
    text = _decode(normalize_newlines(data))
    pairs = _pairs(text)
    doc = DwgDocument()
    blocks: dict[str, list[Entity]] = defaultdict(list)
    block_base: dict[str, tuple[float, float]] = {}
    header: dict[str, list[tuple[str, str]]] = {}
    report: dict = {"skipped": defaultdict(int), "notes": [], "block_expanded": 0}

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


def write_dxf(doc: DwgDocument) -> bytes:
    """Serialise the 2D model into a structurally complete AC1018 ASCII DXF.

    LibreDWG's DXF importer requires a BLOCK_RECORD table, unique handles and
    owner (330) references. Without them dwgwrite reports
    "dwg.block_control ... missing" and silently drops every entity. The
    skeleton below mirrors a dwgread -O DXF export and round-trips R2004.
    """
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
    w.p(9, "$INSUNITS"); w.p(70, doc.units)
    w.p(9, "$MEASUREMENT"); w.p(70, 1)
    w.p(9, "$HANDSEED"); w.p(5, "__HANDSEED__")
    b = doc.bounds() or (0, 0, 100, 100)
    w.p(9, "$EXTMIN"); w.p(10, f"{b[0]:.6f}"); w.p(20, f"{b[1]:.6f}"); w.p(30, 0)
    w.p(9, "$EXTMAX"); w.p(10, f"{b[2]:.6f}"); w.p(20, f"{b[3]:.6f}"); w.p(30, 0)
    w.p(0, "ENDSEC")

    w.p(0, "SECTION"); w.p(2, "TABLES")
    w.p(0, "TABLE"); w.p(2, "LTYPE"); w.p(5, "3"); w.p(330, "0")
    w.p(100, "AcDbSymbolTable"); w.p(70, 1)
    w.p(0, "LTYPE"); w.p(5, "30"); w.p(330, "3")
    w.p(100, "AcDbSymbolTableRecord"); w.p(100, "AcDbLinetypeTableRecord")
    w.p(2, "CONTINUOUS"); w.p(70, 0); w.p(3, "Solid line")
    w.p(72, 65); w.p(73, 0); w.p(40, 0)
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

    w.p(0, "SECTION"); w.p(2, "ENTITIES")
    for e in doc.entities:
        d = e.data
        k = e.kind
        if k == "LINE":
            begin("LINE", e)
            w.xyz(*d["a"]); w.xyz(*d["b"], xcode=11, ycode=21)
        elif k in ("LWPOLYLINE", "POLYLINE", "SPLINE", "HATCH"):
            points = list(d.get("pts", [])) or (d.get("paths", [None])[0] or [])
            points = [tuple(p[:2]) for p in points if p is not None]
            if len(points) < 2:
                continue
            begin("LWPOLYLINE", e)
            w.p(90, len(points)); w.p(70, 1 if (d.get("closed") or k == "HATCH") else 0)
            for pt in points:
                w.p(10, f"{pt[0]:.6f}"); w.p(20, f"{pt[1]:.6f}")
                bulge = pt[2] if len(pt) > 2 else 0.0
                if bulge:
                    w.p(42, f"{bulge:.6f}")
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
            begin("TEXT", e); w.xyz(*d["p"])
            w.p(40, f"{d.get('height', 2.5):.6f}")
            w.p(1, _dxf_text(d.get("text", "")))
            w.p(50, f"{d.get('rotation', 0.0):.6f}")
            w.p(7, "STANDARD")
        elif k == "SOLID":
            pts = list(d.get("pts", []))
            if len(pts) < 3:
                continue
            begin("SOLID", e)
            for idx, pt in enumerate(pts[:4]):
                w.xyz(pt[0], pt[1], xcode=10 + idx, ycode=20 + idx)
    w.p(0, "ENDSEC")
    w.p(0, "EOF")
    return "".join(w.lines).replace("__HANDSEED__", format(next_handle, "X")).encode("utf-8")
