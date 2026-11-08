# SPDX-License-Identifier: GPL-3.0-or-later
"""Load compiled Qt Designer UI, recompiling automatically when a .ui changed.

The .ui XML files are the source of truth and can be edited by hand or in
pyside6-designer. scripts/compile_ui.py (or this auto-check) turns them into
Python modules under src/ilovecad/ui/generated/.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent
GEN_DIR = UI_DIR / "generated"
ROOT = UI_DIR.parents[2]

UI_CLASSES = {
    "main_window": "Ui_MainWindow",
    "pdf_export": "Ui_PdfExportDialog",
    "import_report": "Ui_ImportReportDialog",
    "about": "Ui_AboutDialog",
    "settings": "Ui_SettingsDialog",
    "batch_convert": "Ui_BatchConvertDialog",
    "layer_manager": "Ui_LayerManagerDialog",
}


def needs_compile() -> bool:
    for ui in UI_DIR.glob("*.ui"):
        gen = GEN_DIR / f"ui_{ui.stem}.py"
        if not gen.exists() or gen.stat().st_mtime < ui.stat().st_mtime:
            return True
    return False


def ensure_compiled() -> None:
    if not needs_compile():
        return
    script = ROOT / "scripts" / "compile_ui.py"
    result = subprocess.run([sys.executable, str(script)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("UI 编译失败：\n" + result.stderr.strip()[-1500:])
    print(result.stdout.strip())


def load_ui(widget, name: str):
    ensure_compiled()
    src_dir = str(GEN_DIR.parents[2])
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    module = importlib.import_module(f"ilovecad.ui.generated.ui_{name}")
    ui = getattr(module, UI_CLASSES[name])()
    ui.setupUi(widget)
    return ui
