# SPDX-License-Identifier: GPL-3.0-or-later
"""Version and hard architectural constants.

TARGET_VERSION is the single source of truth for the write path. There is
intentionally no setting, environment variable or command-line flag that can
change it: IloveCAD always writes R2004 (AC1018) DWG files.
"""
from __future__ import annotations

APP_NAME = "IloveCAD"
APP_VERSION = "0.1.0"
LICENSE_SPDX = "GPL-3.0-or-later"

TARGET_VERSION = "AC1018"
TARGET_VERSION_FLAG = "r2004"
TARGET_VERSION_LABEL = "R2004 / AC1018"

# Versions above this require source-side downgrading before decoding.
MAX_DIRECT_READ_VERSION = "AC1018"

VERSION_ORDER = [
    "AC1006", "AC1009", "AC1012", "AC1014", "AC1015", "AC1018",
    "AC1021", "AC1024", "AC1027", "AC1032",
]
VERSION_LABELS = {
    "AC1012": "R13", "AC1014": "R14", "AC1015": "R2000",
    "AC1018": "R2004", "AC1021": "R2007", "AC1024": "R2010",
    "AC1027": "R2013", "AC1032": "R2018",
}

# 3D and non-2D objects are deliberately skipped, never rendered or written.
SKIPPED_3D_TYPES = {
    "3DFACE", "3DSOLID", "REGION", "BODY", "EXTRUDEDSURFACE",
    "LOFTSURFACE", "REVOLVEDSURFACE", "SWEPTSURFACE", "PLANESURFACE",
    "SUBDMESH", "MESH", "ACSH_BREP_CLASS", "SURFACE", "HELIX",
    "POLYLINE_3D", "LIGHT", "CAMERA", "VISUALSTYLE", "MATERIAL",
    "RENDERSETTINGS", "MENTALRAYRENDERSETTINGS", "RAPIDRTRENDER",
}
