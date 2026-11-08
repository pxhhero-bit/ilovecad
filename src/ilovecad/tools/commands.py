# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo / redo stack for lightweight 2D editing."""
from __future__ import annotations

import pickle
from dataclasses import dataclass

from ..model.document import DwgDocument


@dataclass
class _Step:
    label: str
    before: bytes
    after: bytes


class UndoStack:
    """Snapshots entities AND layer definitions.

    Layer rename / delete / style edits mutate entities too, so restoring only
    the entity list would leave dangling layer references. Both are restored
    atomically. Units / precision are document metadata, not undoable edits.
    """

    def __init__(self, doc: DwgDocument | None = None):
        self.doc = doc
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self.limit = 80

    def set_document(self, doc: DwgDocument) -> None:
        self.doc = doc
        self.clear()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def _snapshot(self) -> bytes:
        """Compact snapshot of entities + layers.

        A real drawing can hold tens of thousands of entities; two live
        deepcopies per step would consume hundreds of MB. Serialising to a
        byte string keeps the same atomic restore semantics at a fraction of
        the memory. The bytes are produced here and never loaded from a file,
        so no untrusted pickle data is ever deserialised.
        """
        if self.doc is None:
            return b""
        return pickle.dumps((self.doc.entities, self.doc.layers),
                            protocol=pickle.HIGHEST_PROTOCOL)

    def _restore(self, snap: bytes) -> None:
        if self.doc is None or not snap:
            return
        entities, layers = pickle.loads(snap)
        self.doc.entities = entities
        self.doc.layers = layers

    def _effective_limit(self) -> int:
        count = len(self.doc.entities) if self.doc is not None else 0
        if count > 20000:
            return min(self.limit, 8)
        if count > 5000:
            return min(self.limit, 20)
        if count > 1000:
            return min(self.limit, 40)
        return self.limit

    def perform(self, label: str, fn) -> bool:
        if self.doc is None:
            return False
        before = self._snapshot()
        fn()
        after = self._snapshot()
        if before == after:
            return False
        self._undo.append(_Step(label, before, after))
        limit = self._effective_limit()
        while len(self._undo) > limit:
            self._undo.pop(0)
        self._redo.clear()
        return True

    def undo(self) -> str | None:
        if not self._undo or self.doc is None:
            return None
        step = self._undo.pop()
        self._restore(step.before)
        self._redo.append(step)
        return step.label

    def redo(self) -> str | None:
        if not self._redo or self.doc is None:
            return None
        step = self._redo.pop()
        self._restore(step.after)
        self._undo.append(step)
        return step.label

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""
