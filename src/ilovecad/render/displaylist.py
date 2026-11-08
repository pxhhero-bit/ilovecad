# SPDX-License-Identifier: GPL-3.0-or-later
"""The unified DisplayList consumed by BOTH the screen and the PDF paths."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import geometry as g
from ..model.document import DwgDocument
from .colors import entity_color

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


@dataclass
class PaintText:
    pos: Point
    text: str
    height: float                # in model units
    color: tuple[int, int, int]
    rotation: float = 0.0
    annotation: bool = False


@dataclass
class DisplayList:
    paths: list[PaintPath] = field(default_factory=list)
    texts: list[PaintText] = field(default_factory=list)
    bounds: tuple[float, float, float, float] | None = None


def _line_width(ent, doc, use_lineweight: bool) -> float:
    if not use_lineweight:
        return 0.0
    mm = ent.lineweight or doc.layer_of(ent).lineweight or 0.25
    return max(mm / g.UNIT_MM.get(doc.units, 1.0), 1e-6)


def build(doc: DwgDocument, *, dark: bool = False, monochrome: bool = False,
          include_measurements: bool = True, use_lineweight: bool = True,
          only_visible: bool = True) -> DisplayList:
    dl = DisplayList()
    for ent in doc.entities:
        if only_visible and not doc.visible(ent):
            continue
        color = entity_color(ent, doc, dark, monochrome)
        width = _line_width(ent, doc, use_lineweight)
        dashed = ent.linetype.upper() not in ("BYLAYER", "CONTINUOUS")
        for pts, closed in ent.polylines():
            if len(pts) < 2:
                continue
            dl.paths.append(PaintPath(
                points=[tuple(p) for p in pts], color=color, width=width,
                closed=closed, dashed=dashed,
                filled=ent.kind in ("SOLID", "HATCH") and closed,
                selected=ent.selected))
        if ent.kind in ("TEXT", "MTEXT", "DIMENSION"):
            d = ent.data
            dl.texts.append(PaintText(
                pos=tuple(d.get("p", (0, 0))), text=str(d.get("text", "")),
                height=float(d.get("height", 2.5)) or 2.5,
                color=color, rotation=float(d.get("rotation", 0.0))))

    if include_measurements:
        for m in doc.measurements:
            # Angle measurements supply several strokes (two arms plus an arc);
            # older measurement kinds keep a single legacy "points" polyline.
            sequences = m.get("paths") or [m.get("points", [])]
            all_points: list[Point] = []
            acolor = (220, 60, 40) if not monochrome else (0, 0, 0)
            for idx, seq in enumerate(sequences):
                pts = [tuple(p) for p in seq]
                if len(pts) < 2:
                    continue
                all_points.extend(pts)
                dl.paths.append(PaintPath(
                    points=pts, color=acolor, width=_annotation_width(doc),
                    closed=bool(m.get("closed")) if len(sequences) == 1 else False,
                    annotation=True))
            label = str(m.get("label", ""))
            if label and all_points:
                anchor = m.get("label_at")
                if anchor:
                    cx, cy = float(anchor[0]), float(anchor[1])
                else:
                    cx = sum(p[0] for p in all_points) / len(all_points)
                    cy = sum(p[1] for p in all_points) / len(all_points)
                dl.texts.append(PaintText(pos=(cx, cy), text=label,
                                          height=_annotation_height(doc),
                                          color=acolor, annotation=True))
    dl.bounds = doc.bounds(only_visible=only_visible)
    return dl


def _annotation_width(doc: DwgDocument) -> float:
    b = doc.bounds() or (0, 0, 100, 100)
    return max((b[2] - b[0]) / 900.0, 1e-6)


def _annotation_height(doc: DwgDocument) -> float:
    b = doc.bounds() or (0, 0, 100, 100)
    return max((b[3] - b[1]) / 60.0, 1e-6)
