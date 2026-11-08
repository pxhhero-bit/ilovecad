# SPDX-License-Identifier: GPL-3.0-or-later
"""Local-only preference persistence for object snapping.

A plain INI file in the user scope: no account, no cloud sync, no telemetry,
no network access.  A read-only home directory must never break the viewer.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings

from .tools.snapping import DEFAULT_MODES, SNAP_KINDS, SnapConfig

_KEY_ENABLED = "snapping/enabled"
_KEY_RADIUS = "snapping/radius_px"
_KEY_MODE = "snapping/mode/"


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
