# SPDX-License-Identifier: GPL-3.0-or-later
"""Qt-free 2D view transform (model units <-> screen pixels)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ViewTransform:
    scale: float = 1.0            # screen pixels per model unit
    cx: float = 0.0               # model coordinate at viewport centre
    cy: float = 0.0
    width: int = 800
    height: int = 600

    def to_screen(self, p) -> tuple[float, float]:
        return ((p[0] - self.cx) * self.scale + self.width / 2.0,
                -(p[1] - self.cy) * self.scale + self.height / 2.0)

    def to_model(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.width / 2.0) / self.scale + self.cx,
                -(y - self.height / 2.0) / self.scale + self.cy)

    def fit(self, bounds, margin: float = 0.07) -> None:
        if not bounds:
            self.cx = self.cy = 0.0
            self.scale = 1.0
            return
        x0, y0, x1, y1 = bounds
        w = max(x1 - x0, 1e-6)
        h = max(y1 - y0, 1e-6)
        self.cx, self.cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        self.scale = min(self.width / (w * (1 + 2 * margin)),
                         self.height / (h * (1 + 2 * margin)))
        self.scale = max(min(self.scale, 1e7), 1e-7)

    def zoom(self, factor: float, anchor: tuple[float, float] | None = None) -> None:
        before = self.to_model(*anchor) if anchor else None
        self.scale = max(min(self.scale * factor, 1e9), 1e-9)
        if before:
            after = self.to_model(*anchor)
            self.cx += before[0] - after[0]
            self.cy += before[1] - after[1]

    def pan(self, dx_px: float, dy_px: float) -> None:
        self.cx -= dx_px / self.scale
        self.cy += dy_px / self.scale

    def visible_bounds(self) -> tuple[float, float, float, float]:
        a = self.to_model(0, self.height)
        b = self.to_model(self.width, 0)
        return (a[0], a[1], b[0], b[1])
