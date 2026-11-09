# SPDX-License-Identifier: GPL-3.0-or-later
"""Central CJK-aware font fallback policy and CAD style -> Qt font mapping.

A comma-separated string such as "Microsoft YaHei, Segoe UI, sans-serif"
is NOT a font list in Qt: QFont treats it as one literal family name and
silently falls back to a font that often has no CJK glyphs (rendered as
empty boxes). Qt's real fallback mechanism is QFont.setFamilies([...]).

DWG/DXF text styles reference SHX files (isocp.shx, hztxt.shx, gbcbig.shx),
TrueType names (SimHei, KaiTi, FangSong_GB2312) or MTEXT inline font names.
SHX outlines are not redistributable and are not installed on the host, so
they are mapped to the closest locally available CJK/Latin families.  The
resolved list is passed to QFont.setFamilies: Qt uses the first family for
Latin glyphs and falls back to the later ones for CJK glyphs.
"""
from __future__ import annotations

from PySide6.QtGui import QFont

FALLBACK_FAMILIES = [
    "Microsoft YaHei", "微软雅黑", "Microsoft JhengHei", "SimHei",
    "SimSun", "Noto Sans CJK SC", "Source Han Sans SC",
    "Segoe UI", "Arial", "sans-serif",
]

# Normalised SHX / TrueType name -> ordered candidate families.  The first
# entry is the closest visual match; the rest are fallbacks for missing fonts
# and for glyphs the first family cannot cover (notably CJK).
_LATIN = ["Arial", "Segoe UI", "Helvetica", "sans-serif"]
_MONO = ["Consolas", "Courier New", "monospace"]
_SONG = ["SimSun", "宋体", "Songti SC", "Noto Serif CJK SC", "Microsoft YaHei"]
_HEI = ["SimHei", "黑体", "Microsoft YaHei", "微软雅黑", "Noto Sans CJK SC"]
_KAI = ["KaiTi", "楷体", "STKaiti", "Kaiti SC", "SimSun"]
_FANG = ["FangSong", "仿宋", "STFangsong", "FangSong_GB2312", "SimSun"]
_YAHEI = ["Microsoft YaHei", "微软雅黑", "SimHei", "Noto Sans CJK SC"]

FONT_ALIASES: dict[str, list[str]] = {
    # Latin / ISO SHX shapes
    "txt": _LATIN, "txt.shx": _LATIN, "monotxt": _MONO,
    "simplex": _LATIN, "isocp": _LATIN, "isoct": _LATIN,
    "romans": _LATIN, "romand": _LATIN, "romant": _LATIN,
    "italic": _LATIN, "italict": _LATIN, "italic.shx": _LATIN,
    "gbenor": _LATIN, "gbeitc": _LATIN, "gbcbig": _SONG,
    "arial": _LATIN, "roman": _LATIN,
    # CJK bigfonts / SHX
    "hztxt": _SONG, "hztxt.shx": _SONG, "hztxt_e": _SONG,
    "chineset": _SONG, "hztxt1": _SONG, "hz": _SONG,
    "hzfs": _FANG, "hzfs.shx": _FANG, "hzdxt": _FANG,
    "hyswlongfangsong": _FANG, "pdf 汉仪长仿宋体": _FANG,
    # TrueType / system families
    "simsun": _SONG, "宋体": _SONG, "songti": _SONG,
    "nsimsun": _SONG, "新宋体": _SONG,
    "simhei": _HEI, "黑体": _HEI, "heiti": _HEI,
    "kaiti": _KAI, "楷体": _KAI, "simkai": _KAI, "kai": _KAI,
    "fangsong": _FANG, "仿宋": _FANG, "fangsong_gb2312": _FANG,
    "stfangsong": _FANG, "fs": _FANG,
    "microsoft yahei": _YAHEI, "微软雅黑": _YAHEI,
    "細明體": _SONG, "mingliu": _SONG, "pmingliu": _SONG,
    "等线": _YAHEI, "dengxian": _YAHEI,
}


def normalize_font_name(name: str) -> str:
    """Strip a path, extension and MTEXT inline flags from a font name."""
    raw = str(name or "").strip().strip('"').replace("\\", "/")
    raw = raw.rsplit("/", 1)[-1]
    raw = raw.split("|", 1)[0].strip()
    for ext in (".shx", ".ttf", ".ttc", ".otf"):
        if raw.lower().endswith(ext):
            raw = raw[:-len(ext)]
    return raw.strip().lower()


def font_families(primary: str = "", bigfont: str = "",
                  inline: str = "", style_name: str = "") -> list[str]:
    """Resolve a CAD text style to an ordered Qt family fallback list.

    ``inline`` (``{\\fSimHei|b1|i0|...;...}``) wins over the style's primary
    font, because MTEXT formatting overrides its text style. Some exporters
    leave group 3 empty but encode the font in the STYLE *name* (e.g.
    「PDF 汉仪长仿宋体」), so the style name is also consulted. No
    QFontDatabase query is performed here, so this stays safe in headless
    import/export.
    """
    p = normalize_font_name(primary)
    b = normalize_font_name(bigfont)
    i = normalize_font_name(inline)
    s = normalize_font_name(style_name)
    out: list[str] = []
    if i and i in FONT_ALIASES:
        out.extend(FONT_ALIASES[i])
    if p in FONT_ALIASES:
        out.extend(FONT_ALIASES[p])
    if b in FONT_ALIASES:
        out.extend(FONT_ALIASES[b])
    if s in FONT_ALIASES:
        out.extend(FONT_ALIASES[s])
    out.extend(FALLBACK_FAMILIES)
    # Stable de-duplication, preserving the closest match first.
    seen: set[str] = set()
    unique = []
    for family in out:
        if family and family not in seen:
            seen.add(family)
            unique.append(family)
    return unique or FALLBACK_FAMILIES


def ui_font(point_size: float = 9) -> QFont:
    font = QFont()
    font.setFamilies(FALLBACK_FAMILIES)
    font.setPointSizeF(point_size)
    return font


def drawing_font(point_size: float = 10, pixel_size: int | None = None,
                 families: list[str] | None = None,
                 bold: bool = False, italic: bool = False,
                 underline: bool = False, strikeout: bool = False) -> QFont:
    """A drawing font with source style flags and CJK fallback families."""
    font = QFont()
    font.setFamilies(families or FALLBACK_FAMILIES)
    font.setBold(bold)
    font.setItalic(italic)
    font.setUnderline(underline)
    font.setStrikeOut(strikeout)
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    else:
        font.setPointSizeF(point_size)
    return font
