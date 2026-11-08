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
    units: int = 4               # $INSUNITS used for display / measurement
    source_units: int | None = None  # verbatim $INSUNITS from the source file
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

    def editable(self, ent: Entity) -> bool:
        """Locked layers stay visible and snappable but cannot be edited."""
        layer = self.layers.get(ent.layer)
        return True if layer is None else not layer.locked

    def locked_layers(self) -> set[str]:
        return {name for name, layer in self.layers.items() if layer.locked}

    def bounds(self, only_visible: bool = False):
        b = None
        for e in self.entities:
            if only_visible and not self.visible(e):
                continue
            b = g.expand_bounds(b, e.bounds())
        return b

    def robust_bounds(self, trim: float = 0.01):
        """Return ``(bounds, ignored_count)`` for a usable "zoom extents".

        Real drawings often contain a few stray objects thousands of units
        away from the actual sheet. Fitting to the literal extents then squashes
        the whole drawing into a few pixels. Trim only when the outliers are
        extreme (>3x the dense area); PDF full-extent export still uses
        ``bounds()`` so nothing is silently omitted from the printed output.
        """
        full = self.bounds()
        xs: list[float] = []
        ys: list[float] = []
        for e in self.entities:
            b = e.bounds()
            if b:
                xs.extend((b[0], b[2]))
                ys.extend((b[1], b[3]))
        if not full or len(xs) < 40:
            return full, 0
        xs.sort()
        ys.sort()

        def quantile(values, p):
            return values[min(len(values) - 1,
                             max(0, int(round((len(values) - 1) * p))))]

        x0, x1 = quantile(xs, trim), quantile(xs, 1 - trim)
        y0, y1 = quantile(ys, trim), quantile(ys, 1 - trim)
        fw, fh = max(full[2] - full[0], 1e-6), max(full[3] - full[1], 1e-6)
        tw, th = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        if fw <= 3 * tw and fh <= 3 * th:
            return full, 0
        trimmed = (min(x0, full[0]) if fw <= 3 * tw else x0,
                   min(y0, full[1]) if fh <= 3 * th else y0,
                   max(x1, full[2]) if fw <= 3 * tw else x1,
                   max(y1, full[3]) if fh <= 3 * th else y1)
        ignored = 0
        for e in self.entities:
            b = e.bounds()
            if b and (b[2] < trimmed[0] or b[0] > trimmed[2] or
                      b[3] < trimmed[1] or b[1] > trimmed[3]):
                ignored += 1
        return trimmed, ignored

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
