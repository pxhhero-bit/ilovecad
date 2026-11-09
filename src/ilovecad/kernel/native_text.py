# SPDX-License-Identifier: GPL-3.0-or-later
"""Recover native DWG text geometry when a DXF converter mangles MTEXT.

``dwgread -O DXF`` (and DWGs rebuilt from that legacy output by
``dwgwrite -I DXF``) emit a pre-R2000 MTEXT dialect:

    group 40 = reference rectangle WIDTH, not the text height
    group 41 = reference rectangle HEIGHT
    group 44 = reference rectangle WIDTH ( masquerading as line spacing )
    group 71/72/73 = shifted attachment / direction flags

Taking group 40 at face value turns a 89-unit title-block label into a
3357-unit monster.  The full-fidelity ``dwg2dxf`` writer does not have this
bug, but the source-side R2004 downgrade fallback still goes through
``dwgwrite -I DXF`` and reintroduces it.  ``dwgread -O minJSON`` exposes the
untouched DWG fields (``text_height``, ``attachment``, ``rect_width`` …), so
we join them back by entity handle whenever the legacy dialect is detected.

This module never modifies the source DWG; it only corrects the in-memory 2D
read model.  All values are viewer-side and are also written back on save,
because they are the true source values.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def legacy_mtext_dialect(data: bytes) -> bool:
    """Cheap scan for LibreDWG's legacy MTEXT group-code dialect."""
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    pairs = [(lines[i].strip(), lines[i + 1].rstrip("\r"))
             for i in range(0, len(lines) - 1, 2)]
    i = 0
    while i < len(pairs):
        code, value = pairs[i]
        if code == "0" and value.strip().upper() == "MTEXT":
            j = i + 1
            g40 = g41 = g44 = None
            while j < len(pairs) and pairs[j][0] not in ("0", "9"):
                c, v = pairs[j]
                try:
                    f = float(v)
                except ValueError:
                    j += 1
                    continue
                # Only the FIRST occurrence is authoritative.  dwg2dxf may
                # append an embedded proxy object that repeats the legacy
                # dialect; matching its values would trigger a needless JSON
                # read on every perfectly good file.
                if c == "40" and g40 is None:
                    g40 = f
                elif c == "41" and g41 is None:
                    g41 = f
                elif c == "44" and g44 is None:
                    g44 = f
                j += 1
            g40 = g40 or 0.0
            g41 = g41 or 0.0
            g44 = g44 or 0.0
            # Correct DXF: 40 = text height, 41 = rect width, 44 = 1.0.
            # Legacy DXF: 40 = 44 = rect width and 41 = rect height (often 0).
            if g44 > 8 and (abs(g40 - g44) < 1e-6 or g40 > 1000):
                return True
            if g40 > 1000 and g41 <= 1e-9:
                return True
            i = j
        else:
            i += 1
    return False


def load_native_text(source: Path, out: Path, run) -> dict[int, dict]:
    """Run ``dwgread -O minJSON`` and index MTEXT/TEXT objects by handle."""
    run("dwgread", ["-O", "minJSON", "-o", str(out), str(source)],
        allow_log_errors=True)
    if not out.exists() or out.stat().st_size == 0:
        return {}
    try:
        payload = json.loads(out.read_text(encoding="utf-8", errors="replace"))
    except (ValueError, OSError):
        return {}
    result: dict[int, dict] = {}
    for obj in payload.get("OBJECTS", []):
        kind = obj.get("entity") or obj.get("object")
        if kind not in ("MTEXT", "TEXT", "ATTRIB", "ATTDEF"):
            continue
        handle = obj.get("handle")
        if not isinstance(handle, list) or not handle:
            continue
        try:
            key = int(handle[-1])
        except (TypeError, ValueError):
            continue
        if kind == "MTEXT":
            height = float(obj.get("text_height") or 0.0)
            ext = float(obj.get("extents_height") or 0.0)
            raw = str(obj.get("text") or "")
            corrected = False
            if height > 0 and ext > 0 and height > 3.0 * ext:
                n_lines = max(1, raw.count("\\P") + raw.count("\\p") + 1)
                height = ext / n_lines
                corrected = True
            rect_width = float(obj.get("rect_width") or 0.0)
            info = {
                "kind": "MTEXT",
                "height": height,
                # A rebuilt DWG also mangles the attachment enum; the legacy
                # DXF group 72 is authoritative there (see dxf_codec).
                "align": None if corrected else int(obj.get("attachment") or 1),
                "rect_width": rect_width,
                "line_spacing": float(obj.get("linespace_factor") or 1.0),
                "ins_pt": obj.get("ins_pt"),
                "text": str(obj.get("text") or ""),
            }
        else:
            info = {
                "kind": "TEXT",
                "height": float(obj.get("height") or 0.0),
                "align": None,
                "rect_width": 0.0,
                "line_spacing": 1.0,
                "ins_pt": obj.get("ins_pt"),
                "text": str(obj.get("text_value") or ""),
            }
        if key not in result or info["height"] > result[key]["height"]:
            result[key] = info
    return result


def apply_native_text(doc, mapping: dict[int, dict]) -> int:
    """Overwrite corrupted MTEXT height / attachment values. Returns fixes."""
    if not mapping:
        return 0
    fixed = 0
    for ent in doc.entities:
        if ent.data.get("source_kind") != "MTEXT":
            continue
        key = ent.data.get("handle")
        info = mapping.get(key) if isinstance(key, int) else None
        if not info or info.get("kind") != "MTEXT":
            continue
        d = ent.data
        height = float(info.get("height") or 0.0)
        if height > 1e-6:
            d["height"] = height
            d["native_height"] = True
        attach = info.get("align")
        if attach is not None:
            d["align"] = max(1, min(9, int(attach)))
        rect_width = float(info.get("rect_width") or 0.0)
        # DXF group 41 is the reference rectangle width in DRAWING units.
        # Convert it to an approximate character count for MTEXT wrapping.
        if height > 1e-6 and rect_width > height:
            d["column_chars"] = rect_width / (height * 0.6)
        else:
            d["column_chars"] = 0.0
        spacing = float(info.get("line_spacing") or 1.0)
        if not math.isfinite(spacing) or spacing <= 0:
            spacing = 1.0
        d["line_spacing"] = max(0.5, min(3.0, spacing))
        pt = info.get("ins_pt")
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                d["p"] = (float(pt[0]), float(pt[1]))
            except (TypeError, ValueError):
                pass
        fixed += 1
    return fixed
