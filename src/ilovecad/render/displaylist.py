# SPDX-License-Identifier: GPL-3.0-or-later
"""The unified DisplayList consumed by BOTH the screen and the PDF paths."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import geometry as g
from ..model.document import DwgDocument
from ..fonts import font_families
from .colors import aci_rgb, entity_color

Point = tuple[float, float]


@dataclass
class PaintPath:
    points: list[Point]
    color: tuple[int, int, int]
    width: float                 # in model units
    closed: bool = False
    dashed: bool = False
    filled: bool = False
    selected: bool = False
    annotation: bool = False
    kind: str = ""


@dataclass
class PaintText:
    pos: Point
    text: str
    height: float                # in model units
    color: tuple[int, int, int]
    rotation: float = 0.0
    annotation: bool = False
    lines: list[str] = field(default_factory=list)
    align: int = 1               # attachment point 1..9 (MTEXT 71 / TEXT 72+73)
    mtext: bool = False
    baseline: bool = False       # TEXT group 73=0: insertion IS the baseline
    families: list[str] = field(default_factory=list)
    width_scale: float = 1.0
    oblique: float = 0.0
    line_spacing: float = 1.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikeout: bool = False
    source_height: float = 0.0


@dataclass
class DisplayList:
    paths: list[PaintPath] = field(default_factory=list)
    texts: list[PaintText] = field(default_factory=list)
    bounds: tuple[float, float, float, float] | None = None
    fit_bounds: tuple[float, float, float, float] | None = None


def _line_width(ent, doc, use_lineweight: bool) -> float:
    if not use_lineweight:
        return 0.0
    mm = ent.lineweight or doc.layer_of(ent).lineweight or 0.25
    return max(mm / g.UNIT_MM.get(doc.units, 1.0), 1e-6)


def _hatch_spacing(paths) -> float:
    pts = [tuple(p[:2]) for path in paths for p in path if len(p) >= 2]
    b = g.bounds_of(pts)
    if not b:
        return 0.0
    return max(max(b[2] - b[0], b[3] - b[1]) / 26.0, 1e-6)


def build(doc: DwgDocument, *, dark: bool = False, monochrome: bool = False,
          include_measurements: bool = True, use_lineweight: bool = True,
          only_visible: bool = True,
          highlight_index: int | None = None) -> DisplayList:
    dl = DisplayList()
    text_cap = (doc.text_height_cap() if doc.text_height_mode != "exact"
                else None)
    for ent in doc.entities:
        if only_visible and not doc.visible(ent):
            continue
        color = entity_color(ent, doc, dark, monochrome)
        width = _line_width(ent, doc, use_lineweight)
        dashed = ent.linetype.upper() not in ("BYLAYER", "CONTINUOUS")

        if ent.kind == "HATCH":
            # A HATCH boundary is an outline, not a solid region.  Painting it
            # as a filled polygon produced huge white blobs over the drawing.
            loops = ent.data.get("paths", [])
            for pts, closed in ent.polylines():
                if len(pts) < 2:
                    continue
                dl.paths.append(PaintPath(
                    points=[tuple(p) for p in pts], color=color, width=width,
                    closed=True, dashed=dashed, filled=False,
                    selected=ent.selected, kind="HATCH"))
            spacing = _hatch_spacing(loops)
            if spacing:
                for line in g.hatch_pattern_lines(
                        [[tuple(p) for p in path] for path in loops], spacing):
                    dl.paths.append(PaintPath(
                        points=[tuple(p) for p in line], color=color,
                        width=width * 0.6, closed=False, dashed=False,
                        filled=False, kind="HATCH"))
        else:
            for pts, closed in ent.polylines():
                if len(pts) < 2:
                    continue
                dl.paths.append(PaintPath(
                    points=[tuple(p) for p in pts], color=color, width=width,
                    closed=closed, dashed=dashed,
                    filled=ent.kind == "SOLID" and closed,
                    selected=ent.selected, kind=ent.kind))

        if ent.kind in ("TEXT", "MTEXT", "DIMENSION"):
            d = ent.data
            lines = g.visible_text_lines(str(d.get("text", "")))
            column_chars = float(d.get("column_chars", 0.0) or 0.0)
            if column_chars > 0 and lines:
                lines = g.wrap_text_lines(lines, max(1, int(column_chars)))
            source_height = float(d.get("height", 2.5)) or 0.0
            height = g.adaptive_text_height(
                source_height * float(d.get("height_scale", 1.0) or 1.0),
                text_cap) * doc.text_scale
            if lines and height > 0:
                text_color = color
                override = d.get("color_index")
                if override is not None and not monochrome:
                    text_color = aci_rgb(int(override), dark)
                pos = tuple(d.get("p", (0, 0)))
                off = d.get("display_offset")
                if off:
                    pos = (pos[0] + off[0], pos[1] + off[1])
                dl.texts.append(PaintText(
                    pos=pos, text="\n".join(lines),
                    height=height, color=text_color,
                    rotation=float(d.get("rotation", 0.0)),
                    lines=lines, align=max(1, min(9, int(d.get("align", 1) or 1))),
                    mtext=bool(d.get("mtext")),
                    baseline=bool(d.get("baseline", False)),
                    families=font_families(d.get("font", ""),
                                           d.get("bigfont", ""),
                                           d.get("inline_font", ""),
                                           d.get("style", "")),
                    width_scale=(float(d.get("width_factor", 1.0) or 1.0) *
                                 float(d.get("width_scale", 1.0) or 1.0)),
                    oblique=float(d.get("oblique", 0.0) or 0.0),
                    line_spacing=max(0.5, min(3.0,
                        float(d.get("line_spacing", 1.0) or 1.0))),
                    bold=bool(d.get("bold", False)),
                    italic=bool(d.get("italic", False)),
                    underline=bool(d.get("underline", False)),
                    strikeout=bool(d.get("strikeout", False)),
                    source_height=source_height))

    if include_measurements:
        for index, m in enumerate(doc.measurements):
            # Angle measurements supply several strokes (two arms plus an arc);
            # older measurement kinds keep a single legacy "points" polyline.
            sequences = m.get("paths") or [m.get("points", [])]
            all_points: list[Point] = []
            active = index == highlight_index
            if monochrome:
                acolor = (0, 0, 0)
            else:
                acolor = (255, 170, 30) if active else (220, 60, 40)
            width = _annotation_width(doc) * (2.2 if active else 1.0)
            for seq in sequences:
                pts = [tuple(p) for p in seq]
                if len(pts) < 2:
                    continue
                all_points.extend(pts)
                dl.paths.append(PaintPath(
                    points=pts, color=acolor, width=width,
                    closed=bool(m.get("closed")) if len(sequences) == 1 else False,
                    annotation=True, kind="MEASURE"))
            label = str(m.get("label", ""))
            if label and all_points:
                lines = g.visible_text_lines(label)
                anchor = m.get("label_at")
                if anchor:
                    cx, cy = float(anchor[0]), float(anchor[1])
                else:
                    cx = sum(p[0] for p in all_points) / len(all_points)
                    cy = sum(p[1] for p in all_points) / len(all_points)
                dl.texts.append(PaintText(pos=(cx, cy), text=label,
                                          height=_annotation_height(doc),
                                          color=acolor, annotation=True,
                                          lines=lines or [label]))
    dl.bounds = doc.bounds(only_visible=only_visible)
    dominant, _ = doc.robust_bounds()
    dl.fit_bounds = dominant or dl.bounds
    return dl


def _annotation_width(doc: DwgDocument) -> float:
    b = doc.bounds() or (0, 0, 100, 100)
    return max((b[2] - b[0]) / 900.0, 1e-6)


def _annotation_height(doc: DwgDocument) -> float:
    b = doc.bounds() or (0, 0, 100, 100)
    return max((b[3] - b[1]) / 60.0, 1e-6)
