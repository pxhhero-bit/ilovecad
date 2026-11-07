# SPDX-License-Identifier: GPL-3.0-or-later
"""The in-memory drawing document."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from . import geometry as g
from .entities import Entity


@dataclass
class Layer:
    name: str
    color: int = 7
    linetype: str = "CONTINUOUS"
    lineweight: float = 0.25
    visible: bool = True
    frozen: bool = False
    locked: bool = False


@dataclass
class DwgDocument:
    source_path: str | None = None
    source_version: str = ""
    effective_version: str = ""
    downgraded: bool = False
    units: int = 4               # $INSUNITS, 4 = millimetres
    precision: int = 4
    layers: dict[str, Layer] = field(default_factory=lambda: {"0": Layer("0")})
    entities: list[Entity] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)
    _next_id: int = 1

    # ------------------------------------------------------------------ basics
    def next_id(self) -> int:
        i = self._next_id
        self._next_id += 1
        return i

    def add(self, ent: Entity) -> Entity:
        if ent.eid <= 0:
            ent.eid = self.next_id()
        else:
            self._next_id = max(self._next_id, ent.eid + 1)
        self.entities.append(ent)
        if ent.layer not in self.layers:
            self.layers[ent.layer] = Layer(ent.layer)
        return ent

    def get(self, eid: int) -> Entity | None:
        for e in self.entities:
            if e.eid == eid:
                return e
        return None

    def remove_ids(self, ids: set[int]) -> list[Entity]:
        removed = [e.clone() for e in self.entities if e.eid in ids]
        self.entities = [e for e in self.entities if e.eid not in ids]
        return removed

    def layer_of(self, ent: Entity) -> Layer:
        return self.layers.get(ent.layer, Layer(ent.layer))

    def visible(self, ent: Entity) -> bool:
        layer = self.layers.get(ent.layer)
        return True if layer is None else (layer.visible and not layer.frozen)

    def bounds(self, only_visible: bool = False):
        b = None
        for e in self.entities:
            if only_visible and not self.visible(e):
                continue
            b = g.expand_bounds(b, e.bounds())
        return b

    def entity_count_by_kind(self) -> Counter:
        return Counter(e.kind for e in self.entities)

    def clear_selection(self) -> None:
        for e in self.entities:
            e.selected = False

    def selected_ids(self) -> set[int]:
        return {e.eid for e in self.entities if e.selected}

    def restore(self, entities: list[Entity], ids_removed: set[int]) -> None:
        """Restore a command snapshot."""
        self.entities = [e for e in self.entities if e.eid not in ids_removed]
        by_id = {e.eid: e for e in self.entities}
        for e in entities:
            by_id[e.eid] = e
        self.entities = list(by_id.values())
