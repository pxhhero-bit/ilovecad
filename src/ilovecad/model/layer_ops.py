# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure, Qt-free layer editing operations.

Keeping these operations out of the dialog makes them testable and guarantees
that the same rules are applied by the UI, command aliases and the self test.
Layer edits are document-level changes; the undo stack snapshots layers too.
"""
from __future__ import annotations

from collections import Counter

from .document import DwgDocument, Layer

# DXF layer names cannot contain these characters.
INVALID_CHARS = '<>/\\":;?*|='
LINETYPES = (
    "CONTINUOUS", "DASHED", "HIDDEN", "CENTER", "PHANTOM", "DOT",
    "DASHDOT", "BORDER", "DIVIDE",
)


def valid_name(name: str) -> str:
    """Return a trimmed name or raise ValueError with a Chinese explanation."""
    n = str(name).strip()
    if not n:
        raise ValueError("图层名不能为空")
    if len(n) > 255:
        raise ValueError("图层名过长（最多 255 个字符）")
    bad = [c for c in n if c in INVALID_CHARS or ord(c) < 32]
    if bad:
        raise ValueError("图层名不能包含 " + " ".join(sorted(set(bad))))
    if n.upper() == "0" and n != "0":
        raise ValueError("图层名不能与保留图层 0 混淆")
    return n


def layer_usage(doc: DwgDocument) -> Counter:
    return Counter(e.layer for e in doc.entities)


def create_layer(doc: DwgDocument, name: str, *, color: int = 7,
                 linetype: str = "CONTINUOUS", lineweight: float = 0.25) -> Layer:
    n = valid_name(name)
    if n in doc.layers:
        raise ValueError(f"图层 {n} 已存在")
    layer = Layer(n, color=color, linetype=linetype, lineweight=lineweight,
                  visible=True, frozen=False, locked=False)
    doc.layers[n] = layer
    return layer


def rename_layer(doc: DwgDocument, old: str, new: str) -> int:
    """Rename a layer and migrate every entity on it. Returns entity count."""
    if old == "0":
        raise ValueError("图层 0 是基础图层，不能重命名")
    if old not in doc.layers:
        raise ValueError(f"图层 {old} 不存在")
    n = valid_name(new)
    if n == old:
        return 0
    if n in doc.layers:
        raise ValueError(f"图层 {n} 已存在")
    layer = doc.layers.pop(old)
    layer.name = n
    doc.layers[n] = layer
    moved = 0
    for e in doc.entities:
        if e.layer == old:
            e.layer = n
            moved += 1
    return moved


def delete_layer(doc: DwgDocument, name: str, fallback: str = "0") -> int:
    """Delete a layer, moving its entities to ``fallback``. Returns count."""
    if name == "0":
        raise ValueError("图层 0 是基础图层，不能删除")
    if name not in doc.layers:
        raise ValueError(f"图层 {name} 不存在")
    if fallback not in doc.layers:
        raise ValueError(f"目标图层 {fallback} 不存在")
    if fallback == name:
        raise ValueError("目标图层不能与待删除图层相同")
    moved = 0
    for e in doc.entities:
        if e.layer == name:
            e.layer = fallback
            moved += 1
    del doc.layers[name]
    return moved


def set_flags(doc: DwgDocument, name: str, *, visible: bool | None = None,
              frozen: bool | None = None, locked: bool | None = None) -> Layer:
    layer = doc.layers.get(name)
    if layer is None:
        raise ValueError(f"图层 {name} 不存在")
    if visible is not None:
        layer.visible = bool(visible)
    if frozen is not None:
        layer.frozen = bool(frozen)
    if locked is not None:
        layer.locked = bool(locked)
    return layer


def set_style(doc: DwgDocument, name: str, *, color: int | None = None,
              linetype: str | None = None,
              lineweight: float | None = None) -> Layer:
    layer = doc.layers.get(name)
    if layer is None:
        raise ValueError(f"图层 {name} 不存在")
    if color is not None:
        c = int(color)
        if not 1 <= c <= 255:
            raise ValueError("ACI 颜色号必须在 1–255 之间")
        layer.color = c
    if linetype is not None:
        t = str(linetype).strip().upper()
        if t not in LINETYPES and t != "BYLAYER":
            raise ValueError(f"不支持的线型：{linetype}")
        layer.linetype = "CONTINUOUS" if t == "BYLAYER" else t
    if lineweight is not None:
        w = float(lineweight)
        if not 0 <= w <= 2.11:
            raise ValueError("线宽必须在 0–2.11 mm 之间")
        layer.lineweight = w
    return layer


def all_on(doc: DwgDocument) -> int:
    for layer in doc.layers.values():
        layer.visible, layer.frozen = True, False
    return len(doc.layers)


def invert(doc: DwgDocument) -> int:
    for layer in doc.layers.values():
        layer.visible = not layer.visible
        layer.frozen = not layer.visible
    return len(doc.layers)
