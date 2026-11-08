# SPDX-License-Identifier: GPL-3.0-or-later
"""Undo / redo stack for lightweight 2D editing."""
from __future__ import annotations

import copy
from dataclasses import dataclass

from ..model.document import DwgDocument


@dataclass
class _Step:
    label: str
    before: list
    after: list


class UndoStack:
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

    def _snapshot(self) -> list:
        return copy.deepcopy(self.doc.entities) if self.doc else []

    def perform(self, label: str, fn) -> bool:
        if self.doc is None:
            return False
        before = self._snapshot()
        fn()
        after = self._snapshot()
        if before == after:
            return False
        self._undo.append(_Step(label, before, after))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()
        return True

    def undo(self) -> str | None:
        if not self._undo or self.doc is None:
            return None
        step = self._undo.pop()
        self.doc.entities = copy.deepcopy(step.before)
        self._redo.append(step)
        return step.label

    def redo(self) -> str | None:
        if not self._redo or self.doc is None:
            return None
        step = self._redo.pop()
        self.doc.entities = copy.deepcopy(step.after)
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
