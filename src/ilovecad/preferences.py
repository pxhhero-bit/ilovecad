# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only preferences: object snapping and measurement units / precision.

A plain INI file in the user scope: no account, no cloud sync, no telemetry,
no network access.  A read-only home directory must never break the viewer.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

from .tools.snapping import DEFAULT_MODES, SNAP_KINDS, SnapConfig

_KEY_ENABLED = "snapping/enabled"
_KEY_RADIUS = "snapping/radius_px"
_KEY_MODE = "snapping/mode/"
_KEY_UNITS = "measurement/units"
_KEY_PRECISION = "measurement/precision"
_KEY_TEXT_MODE = "text/height_mode"
_KEY_TEXT_SCALE = "text/display_scale"

# $INSUNITS codes with a known millimetre conversion (see geometry.UNIT_MM).
UNITS_CHOICES = (
    (4, "毫米 (mm)"),
    (5, "厘米 (cm)"),
    (6, "米 (m)"),
    (1, "英寸 (in)"),
    (2, "英尺 (ft)"),
    (10, "码 (yd)"),
    (7, "千米 (km)"),
    (13, "微米 (µm)"),
    (14, "分米 (dm)"),
    (0, "无单位"),
)
DEFAULT_UNITS = 4
DEFAULT_PRECISION = 4


def _settings() -> QSettings:
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                     "IloveCAD", "IloveCAD")


def _as_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_snap_config() -> SnapConfig:
    cfg = SnapConfig()
    try:
        s = _settings()
        cfg.enabled = _as_bool(s.value(_KEY_ENABLED, True))
        cfg.radius_px = float(s.value(_KEY_RADIUS, cfg.radius_px))
        for kind in SNAP_KINDS:
            cfg.modes[kind] = _as_bool(
                s.value(_KEY_MODE + kind, DEFAULT_MODES[kind]),
                DEFAULT_MODES[kind])
    except Exception:
        return SnapConfig()
    return cfg


def save_snap_config(cfg: SnapConfig) -> SnapConfig:
    try:
        s = _settings()
        s.setValue(_KEY_ENABLED, bool(cfg.enabled))
        s.setValue(_KEY_RADIUS, float(cfg.radius_px))
        for kind in SNAP_KINDS:
            s.setValue(_KEY_MODE + kind, bool(cfg.modes.get(kind, False)))
        s.sync()
    except Exception:
        pass
    return cfg


def sanitize_units_precision(units, precision) -> tuple[int, int]:
    try:
        u = int(units)
    except (TypeError, ValueError):
        u = DEFAULT_UNITS
    if u not in {code for code, _ in UNITS_CHOICES}:
        u = DEFAULT_UNITS
    try:
        p = int(precision)
    except (TypeError, ValueError):
        p = DEFAULT_PRECISION
    return u, max(0, min(8, p))


def load_units_precision() -> tuple[int, int]:
    try:
        s = _settings()
        return sanitize_units_precision(
            s.value(_KEY_UNITS, DEFAULT_UNITS),
            s.value(_KEY_PRECISION, DEFAULT_PRECISION))
    except Exception:
        return DEFAULT_UNITS, DEFAULT_PRECISION


def save_units_precision(units: int, precision: int) -> tuple[int, int]:
    u, p = sanitize_units_precision(units, precision)
    try:
        s = _settings()
        s.setValue(_KEY_UNITS, u)
        s.setValue(_KEY_PRECISION, p)
        s.sync()
    except Exception:
        pass
    return u, p


def sanitize_text_settings(mode, scale) -> tuple[str, float]:
    m = "exact" if str(mode).strip().lower() in ("exact", "原样", "1:1") else "auto"
    try:
        v = float(scale)
    except (TypeError, ValueError):
        v = 1.0
    return m, max(0.25, min(3.0, v))


def load_text_settings() -> tuple[str, float]:
    try:
        s = _settings()
        return sanitize_text_settings(
            s.value(_KEY_TEXT_MODE, "auto"),
            s.value(_KEY_TEXT_SCALE, 1.0))
    except Exception:
        return "auto", 1.0


def save_text_settings(mode, scale) -> tuple[str, float]:
    m, v = sanitize_text_settings(mode, scale)
    try:
        s = _settings()
        s.setValue(_KEY_TEXT_MODE, m)
        s.setValue(_KEY_TEXT_SCALE, v)
        s.sync()
    except Exception:
        pass
    return m, v
