# SPDX-License-Identifier: GPL-3.0-or-later
"""Editable 2D entity properties, independent of Qt.

The property panel and the self test share these rules: locked layers are
never edited, values are parsed/validated in one place, and every edit is a
pure document mutation so the caller can wrap it in an undo step.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..model.document import DwgDocument
from ..model.entities import Entity
from ..model.layer_ops import LINETYPES

Point = tuple[float, float]
MULTIPLE = "（多个值）"

LINETYPE_CHOICES = ("BYLAYER",) + LINETYPES


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    value: str
    editable: bool = True


def is_locked(doc: DwgDocument, ent: Entity) -> bool:
    layer = doc.layers.get(ent.layer)
    return bool(layer and layer.locked)


def editable_entities(doc: DwgDocument, entities) -> list[Entity]:
    return [e for e in entities if not is_locked(doc, e)]


def locked_count(doc: DwgDocument, entities) -> int:
    return sum(1 for e in entities if is_locked(doc, e))


def _fmt(value) -> str:
    if isinstance(value, (tuple, list)):
        return ", ".join(f"{float(v):.4f}" for v in value[:2])
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _common(doc: DwgDocument, ents: list[Entity]) -> list[Row]:
    def agreed(fn) -> str:
        values = {fn(e) for e in ents}
        return next(iter(values)) if len(values) == 1 else MULTIPLE

    color = agreed(lambda e: "ByLayer" if e.color in (0, 256) else str(e.color))
    lw = agreed(lambda e: f"{e.lineweight:.2f}")
    return [
        Row("attr:layer", "图层", agreed(lambda e: e.layer)),
        Row("attr:color", "颜色", color),
        Row("attr:linetype", "线型", agreed(lambda e: e.linetype)),
        Row("attr:lineweight", "线宽 (mm)", lw),
    ]


def _geometry(ent: Entity) -> list[Row]:
    """Rows for one selected entity, derived from its raw geometry payload."""
    d, k = ent.data, ent.kind
    specs: list[tuple[str, str, str]] = []
    if k == "LINE":
        specs = [("a", "起点 X,Y", "point"), ("b", "终点 X,Y", "point")]
    elif k in ("CIRCLE", "ARC", "ELLIPSE"):
        specs = [("c", "圆心 X,Y", "point")]
        if k != "ELLIPSE":
            specs.append(("r", "半径", "float"))
    elif k == "POINT":
        specs = [("p", "位置 X,Y", "point")]
    elif k in ("TEXT", "MTEXT", "DIMENSION"):
        specs = [("p", "插入点 X,Y", "point"), ("text", "文字内容", "text"),
                 ("height", "字高", "float"), ("rotation", "旋转角 (°)", "float")]
    if k == "ARC":
        specs += [("start", "起始角 (°)", "float"),
                  ("end", "终止角 (°)", "float")]
    if k in ("LWPOLYLINE", "POLYLINE"):
        specs += [("closed", "闭合", "bool")]
    rows: list[Row] = []
    for field, label, _ in specs:
        if field in d:
            rows.append(Row(f"data:{field}", label, _fmt(d[field])))
    return rows


def rows(doc: DwgDocument, entities) -> list[Row]:
    ents = [e for e in entities if e.selected] if entities else []
    if not ents:
        return [Row("", "提示", "未选中对象", False)]
    out = [
        Row("", "选中数量", str(len(ents)), False),
        Row("", "类型", next(iter({e.kind for e in ents}))
            if len({e.kind for e in ents}) == 1 else MULTIPLE, False),
    ]
    locked = locked_count(doc, ents)
    if locked:
        out.append(Row("", "锁定图层", f"{locked} 个对象不可编辑", False))
    out.extend(_common(doc, ents))
    if len(ents) == 1:
        out.extend(_geometry(ents[0]))
        b = ents[0].bounds()
        if b:
            out.append(Row("", "范围 X", f"{b[0]:.3f} ~ {b[2]:.3f}", False))
            out.append(Row("", "范围 Y", f"{b[1]:.3f} ~ {b[3]:.3f}", False))
    return out


def choices_for(doc: DwgDocument, key: str) -> list[str]:
    if key == "attr:layer":
        return sorted(doc.layers)
    if key == "attr:linetype":
        return list(LINETYPE_CHOICES)
    if key == "data:closed":
        return ["是", "否"]
    return []


def parse_point(raw) -> Point:
    text = str(raw).replace("，", ",").replace(";", ",").strip()
    parts = [p.strip() for p in text.replace(" ", ",").split(",") if p.strip()]
    if len(parts) != 2:
        raise ValueError("坐标必须写成 X,Y（例如 12.5,40）")
    return (float(parts[0]), float(parts[1]))


def parse_bool(raw) -> bool:
    text = str(raw).strip().lower()
    if text in ("是", "true", "yes", "1", "on", "闭合"):
        return True
    if text in ("否", "false", "no", "0", "off", "开放"):
        return False
    raise ValueError("请输入「是」或「否」")


def apply_value(doc: DwgDocument, entities, key: str, raw) -> int:
    """Apply one validated property to unlocked entities. Returns edit count."""
    ents = editable_entities(doc, entities)
    if not ents:
        raise ValueError("所选对象位于锁定图层，未做任何修改")
    text = str(raw).strip()

    def attr(name, value):
        for e in ents:
            setattr(e, name, value)

    if key == "attr:layer":
        if text not in doc.layers:
            raise ValueError(f"图层 {text} 不存在，请先在图层管理器中创建")
        attr("layer", text)
    elif key == "attr:color":
        value = int(text)
        if not 1 <= value <= 256:
            raise ValueError("ACI 颜色号必须在 1–256 之间（256 = ByLayer）")
        attr("color", value)
    elif key == "attr:linetype":
        value = text.upper()
        if value not in LINETYPE_CHOICES:
            raise ValueError("不支持的线型")
        attr("linetype", value)
    elif key == "attr:lineweight":
        value = float(text)
        if not 0 <= value <= 2.11:
            raise ValueError("线宽必须在 0–2.11 mm 之间")
        attr("lineweight", value)
    elif not key.startswith("data:"):
        raise ValueError("该属性为只读")
    else:
        field = key.split(":", 1)[1]
        for e in ents:
            if field not in e.data:
                raise ValueError(f"{e.kind} 没有 {field} 属性")
        if key in ("data:a", "data:b", "data:c", "data:p"):
            value = parse_point(text)
            for e in ents:
                e.data[field] = value
        elif key == "data:text":
            if not text:
                raise ValueError("文字内容不能为空")
            for e in ents:
                e.data[field] = text
        elif key == "data:closed":
            value = parse_bool(text)
            for e in ents:
                e.data[field] = value
        else:
            value = float(text)
            if field == "r" and value <= 0:
                raise ValueError("半径必须大于 0")
            if field == "height" and value <= 0:
                raise ValueError("字高必须大于 0")
            for e in ents:
                e.data[field] = value
    return len(ents)
