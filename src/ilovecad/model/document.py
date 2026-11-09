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
    cell_of: list[set[tuple[int, int]]] = []
    xs: list[float] = []
    ys: list[float] = []
    for b in items:
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        # A long frame / border line has its CENTRE in the middle of the sheet,
        # but its two ends define the sheet edges.  Binning only the centre
        # leaves those edge cells empty, so the flood fill quietly dropped the
        # right border and the bottom title-block row from zoom-extents.
        # Sampling the bbox corners as well keeps long lines attached to the
        # main sheet without merging genuinely separate sheets.
        samples = ((cx, cy), (b[0], b[1]), (b[2], b[1]),
                   (b[0], b[3]), (b[2], b[3]))
        owned: set[tuple[int, int]] = set()
        for px, py in samples:
            ix = min(n_cells - 1, max(0, int((px - full[0]) / fw * (n_cells - 1))))
            iy = min(n_cells - 1, max(0, int((py - full[1]) / fh * (n_cells - 1))))
            key = (ix, iy)
            if key not in owned:
                owned.add(key)
                cells[key] += 1
        cell_of.append(owned)
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
        chosen = [items[i] for i, owned in enumerate(cell_of) if owned & seen]
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
    styles: dict[str, dict] = field(default_factory=dict)
    entities: list[Entity] = field(default_factory=list)
    measurements: list[dict] = field(default_factory=list)
    # Local viewer-only text preferences. Neither value is ever written back
    # to the source file.  "exact" (default) = source heights 1:1; "auto" is
    # an opt-in compression for exporters that emit pathological text heights.
    text_height_mode: str = "exact"
    text_scale: float = 1.0
    # Viewer-only: snap labels to the centre of their title-block grid cell.
    # Cleared / recomputed by model.titleblock.apply_title_block_alignment.
    align_title_text: bool = True
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

        The viewer may compress oversized sheet titles for display only; the
        DXF header written back still describes the real text heights.
        """
        b = None
        for e in self.entities:
            b = g.expand_bounds(b, e.bounds(None, 1.0))
        return b

    def _text_cap(self):
        return self.text_height_cap() if self.text_height_mode != "exact" else None

    def bounds(self, only_visible: bool = False):
        cap = self._text_cap()
        b = None
        for e in self.entities:
            if only_visible and not self.visible(e):
                continue
            b = g.expand_bounds(b, e.bounds(cap, self.text_scale))
        return b

    def sheet_frame_bounds(self):
        """Bounds of the dominant sheet's rectangular border, if it has one.

        A dense-cluster fit cannot see a large, sparse frame: the four border
        lines have their centres far from the dense geometry, so the flood fill
        leaves the outer frame (and the title block attached to its right edge)
        outside zoom-extents.  Real sheets have two nested axis-aligned
        rectangles (paper border + drawing frame).  Detect candidate rectangles
        from long axis-aligned lines, then keep the smallest one that contains
        essentially all of the sheet's entities.
        """
        full = self.bounds()
        if not full:
            return None
        fw, fh = full[2] - full[0], full[3] - full[1]
        if fw <= 1e-9 or fh <= 1e-9:
            return None
        min_len = 0.18 * max(fw, fh)
        hs: set[tuple[float, float, float]] = set()
        vs: set[tuple[float, float, float]] = set()
        for e in self.entities:
            if e.kind == "LINE":
                segs = [(tuple(e.data["a"]), tuple(e.data["b"]))]
            elif e.kind in ("LWPOLYLINE", "POLYLINE"):
                pts = [tuple(p[:2]) for p in e.data.get("pts", [])]
                if len(pts) < 2:
                    continue
                segs = list(zip(pts, pts[1:]))
                if e.data.get("closed") and len(pts) > 2:
                    segs.append((pts[-1], pts[0]))
            else:
                continue
            for (x1, y1), (x2, y2) in segs:
                if abs(y1 - y2) < 1e-6 and abs(x2 - x1) >= min_len:
                    hs.add((round(y1, 4), round(min(x1, x2), 4),
                            round(max(x1, x2), 4)))
                if abs(x1 - x2) < 1e-6 and abs(y2 - y1) >= min_len:
                    vs.add((round(x1, 4), round(min(y1, y2), 4),
                            round(max(y1, y2), 4)))
        if len(hs) < 2 or len(vs) < 2:
            return None
        eps = max(fw, fh) * 1e-5
        candidates = []
        hl, vl = sorted(hs), sorted(vs)
        for yi, (y1, a1, b1) in enumerate(hl):
            for y2, a2, b2 in hl[yi + 1:]:
                if y2 - y1 < min_len:
                    continue
                for xi, (x1, c1, d1) in enumerate(vl):
                    for x2, c2, d2 in vl[xi + 1:]:
                        if x2 - x1 < min_len:
                            continue
                        if not (a1 <= x1 + eps and b1 >= x2 - eps and
                                a2 <= x1 + eps and b2 >= x2 - eps and
                                c1 <= y1 + eps and d1 >= y2 - eps and
                                c2 <= y1 + eps and d2 >= y2 - eps):
                            continue
                        candidates.append((x1, y1, x2, y2))
        if not candidates:
            return None
        centres = []
        for e in self.entities:
            b = e.bounds()
            if b:
                centres.append(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0))
        if len(centres) < 40:
            return None

        def score(rect):
            x0, y0, x1, y1 = rect
            return sum(1 for cx, cy in centres
                       if x0 <= cx <= x1 and y0 <= cy <= y1)

        best = max(score(r) for r in candidates)
        if best < max(40, len(centres) * 0.5):
            return None
        # Prefer the tighter frame: both nested borders contain the same sheet.
        good = [r for r in candidates if score(r) >= best * 0.98]
        return min(good, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))

    def robust_bounds(self, trim: float = 0.02):
        """Return ``(bounds, ignored_count)`` for a usable "zoom extents".

        Real drawings often keep several sheets / title blocks thousands of
        units away from the actual drawing.  Fitting to the literal extents
        squashes the real content into a few pixels.  The dominant spatial
        cluster is used instead; FITALL / PDF「完整范围」still use
        :meth:`bounds` and therefore include every object.
        """
        frame = self.sheet_frame_bounds()
        if frame:
            cap = self._text_cap()
            ignored = 0
            for e in self.entities:
                b = e.bounds(cap, self.text_scale)
                if not b:
                    continue
                cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                if not (frame[0] <= cx <= frame[2] and
                        frame[1] <= cy <= frame[3]):
                    ignored += 1
            return frame, ignored
        cap = self._text_cap()
        items = []
        for e in self.entities:
            b = e.bounds(cap, self.text_scale)
            if b:
                items.append(b)
        full = None
        for b in items:
            full = g.expand_bounds(full, b)
        cluster, ignored, trimmed = _dominant_bounds(items, trim)
        if trimmed and cluster:
            # A title block usually sits below the dense geometry of the same
            # sheet.  Its few text entities form no dense grid cell of their
            # own, so the flood fill can leave them just outside the fit and
            # cut the sheet title in half.  Pull back every entity of that
            # sheet (text AND the thin frame / cell lines) whose centre lies
            # inside the sheet's horizontal span and not absurdly far above /
            # below it; remote sheets stay excluded.  Text alone used to leave
            # the right border and bottom cell row outside the fit.
            x0, y0, x1, y1 = cluster
            w, h = x1 - x0, y1 - y0
            lo_y, hi_y = y0 - 1.5 * h, y1 + 1.5 * h
            for e in self.entities:
                if e.kind not in ("TEXT", "MTEXT", "DIMENSION"):
                    continue
                b = e.bounds(cap, self.text_scale)
                if not b:
                    continue
                cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                if (x0 - 0.1 * w <= cx <= x1 + 0.1 * w and
                        lo_y <= cy <= hi_y):
                    cluster = g.expand_bounds(cluster, b)
            # The title-block GRID lines sit just outside the fitted box.  Add
            # nearby geometry with a tight 2% margin, clamping each entity's
            # box to that margin so a stray full-width line (or the next
            # sheet) cannot drag zoom-extents back out to the whole model.
            x0, y0, x1, y1 = cluster
            w, h = x1 - x0, y1 - y0
            mx, my = max(w * 0.02, 1.0), max(h * 0.02, 1.0)
            region = (x0 - mx, y0 - my, x1 + mx, y1 + my)
            for e in self.entities:
                if e.kind in ("TEXT", "MTEXT", "DIMENSION"):
                    continue
                b = e.bounds(cap, self.text_scale)
                if not b:
                    continue
                cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
                if region[0] <= cx <= region[2] and region[1] <= cy <= region[3]:
                    clipped = (max(b[0], region[0]), max(b[1], region[1]),
                               min(b[2], region[2]), min(b[3], region[3]))
                    if clipped[0] < clipped[2] or clipped[1] < clipped[3]:
                        cluster = g.expand_bounds(cluster, clipped)
            ignored = sum(1 for b in items
                          if b[2] < cluster[0] or b[0] > cluster[2] or
                          b[3] < cluster[1] or b[1] > cluster[3])
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
