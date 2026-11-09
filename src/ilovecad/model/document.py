# SPDX-License-Identifier: GPL-3.0-or-later
"""The in-memory drawing document."""
from __future__ import annotations

from collections import Counter, deque
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


def _dominant_bounds(items, trim: float = 0.02):
    """Bounds of the dominant spatial cluster, or the full bounds.

    Returns ``(bounds, ignored_count, trimmed)``.  Entity centres are binned
    on a 64x64 grid; a flood fill from the densest cell through non-empty
    neighbours finds the main sheet.  Empty cells isolate other sheets.
    """
    full = None
    for b in items:
        full = g.expand_bounds(full, b)
    if not full or len(items) < 40:
        return full, 0, False
    fw, fh = full[2] - full[0], full[3] - full[1]
    if fw <= 1e-9 or fh <= 1e-9:
        return full, 0, False

    n_cells = 64
    cells: Counter = Counter()
    cell_of: list[tuple[int, int]] = []
    xs: list[float] = []
    ys: list[float] = []
    for b in items:
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        ix = min(n_cells - 1, max(0, int((cx - full[0]) / fw * (n_cells - 1))))
        iy = min(n_cells - 1, max(0, int((cy - full[1]) / fh * (n_cells - 1))))
        cells[(ix, iy)] += 1
        cell_of.append((ix, iy))
        xs.extend((b[0], b[2]))
        ys.extend((b[1], b[3]))
    peak, peak_count = cells.most_common(1)[0]
    cluster = None
    if peak_count >= max(5, len(items) * 0.005):
        # Any non-empty neighbouring cell belongs to the main sheet: sparse
        # border entities must not be cut off by an aggressive density cut.
        seen = {peak}
        queue = deque([peak])
        while queue:
            x, y = queue.popleft()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (x + dx, y + dy)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        queue.append(nb)
        chosen = [items[i] for i, c in enumerate(cell_of) if c in seen]
        if len(chosen) >= max(20, len(items) * 0.5):
            b = chosen[0]
            for other in chosen[1:]:
                b = g.expand_bounds(b, other)
            pad_x, pad_y = (b[2] - b[0]) * 0.03, (b[3] - b[1]) * 0.03
            cluster = (b[0] - pad_x, b[1] - pad_y,
                       b[2] + pad_x, b[3] + pad_y)
    if cluster is None:
        xs.sort(); ys.sort()

        def quantile(values, p):
            return values[min(len(values) - 1,
                             max(0, int(round((len(values) - 1) * p))))]

        x0, x1 = quantile(xs, trim), quantile(xs, 1 - trim)
        y0, y1 = quantile(ys, trim), quantile(ys, 1 - trim)
        tw, th = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        if fw <= 1.6 * tw and fh <= 1.6 * th:
            return full, 0, False
        cluster = (x0, y0, x1, y1)
    if (cluster[2] - cluster[0] >= 0.98 * fw and
            cluster[3] - cluster[1] >= 0.98 * fh):
        return full, 0, False
    ignored = sum(1 for b in items
                  if b[2] < cluster[0] or b[0] > cluster[2] or
                  b[3] < cluster[1] or b[1] > cluster[3])
    return cluster, ignored, True


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

    def text_height_cap(self):
        """Visual ceiling for oversized MTEXT, derived from real geometry.

        Some CAD exporters (or a heavily scaled INSERT) emit sheet titles with
        a text height of several thousand drawing units.  Rendering those at
        face value paints giant glyphs over the drawing.  The ceiling is 1/30
        of the dominant *non-text* sheet size; normal annotation is untouched
        and the original height is still written back on save.
        """
        items = []
        for e in self.entities:
            if e.kind in ("TEXT", "MTEXT", "DIMENSION"):
                continue
            b = e.bounds()
            if b:
                items.append(b)
        if len(items) < 20:
            return None
        cluster, _, _ = _dominant_bounds(items)
        if not cluster:
            return None
        return max(min(cluster[2] - cluster[0], cluster[3] - cluster[1]) / 30.0,
                   1e-6)

    def full_bounds(self):
        """Literal extents with the source (uncapped) text heights.

        The viewer caps absurdly large sheet titles for display only; the DXF
        header written back still describes the real text heights.
        """
        b = None
        for e in self.entities:
            b = g.expand_bounds(b, e.bounds(None))
        return b

    def bounds(self, only_visible: bool = False):
        cap = self.text_height_cap()
        b = None
        for e in self.entities:
            if only_visible and not self.visible(e):
                continue
            b = g.expand_bounds(b, e.bounds(cap))
        return b

    def robust_bounds(self, trim: float = 0.02):
        """Return ``(bounds, ignored_count)`` for a usable "zoom extents".

        Real drawings often keep several sheets / title blocks thousands of
        units away from the actual drawing.  Fitting to the literal extents
        squashes the real content into a few pixels.  The dominant spatial
        cluster is used instead; FITALL / PDF「完整范围」still use
        :meth:`bounds` and therefore include every object.
        """
        cap = self.text_height_cap()
        items = []
        for e in self.entities:
            b = e.bounds(cap)
            if b:
                items.append(b)
        full = None
        for b in items:
            full = g.expand_bounds(full, b)
        cluster, ignored, trimmed = _dominant_bounds(items, trim)
        return (cluster, ignored) if trimmed else (full, 0)

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
