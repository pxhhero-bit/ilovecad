# SPDX-License-Identifier: GPL-3.0-or-later
"""AutoCAD Color Index (ACI) approximation and theme-aware colour helpers."""
from __future__ import annotations

BASE = {
    1: (230, 40, 40), 2: (235, 205, 30), 3: (50, 190, 80),
    4: (40, 200, 210), 5: (50, 110, 225), 6: (205, 60, 200),
    7: (25, 25, 28), 8: (110, 110, 115), 9: (175, 175, 180),
}


def aci_rgb(index: int, dark: bool = False) -> tuple[int, int, int]:
    i = int(index) & 0xFF
    if i in (0, 256):
        i = 7
    if i in BASE:
        r, g, b = BASE[i]
    elif 10 <= i <= 249:
        # 10..249 is a 24-hue x 10-value ramp. Approximate with HSV.
        import colorsys
        hue = ((i - 10) // 10) / 24.0
        level = 0.45 + 0.5 * ((i - 10) % 10) / 9.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.75, level)
        r, g, b = int(r * 255), int(g * 255), int(b * 255)
    elif 250 <= i <= 255:
        v = 40 + (i - 250) * 35
        r = g = b = min(v, 215)
    else:
        r = g = b = 128
    if dark and i == 7:
        return (235, 235, 238)
    if dark:
        r, g, b = (min(255, int(c * 1.25 + 30)) for c in (r, g, b))
    return (r, g, b)


def entity_color(ent, doc, dark: bool = False, monochrome: bool = False):
    if monochrome:
        return (0, 0, 0)
    idx = ent.color if ent.color not in (0, 256) else doc.layer_of(ent).color
    return aci_rgb(idx, dark)
