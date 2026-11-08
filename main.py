#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""IloveCAD 根入口。

    python main.py                     # 启动主窗口
    python main.py 图纸.dwg            # 启动并直接打开图纸
    python main.py 图纸.dwg 图纸.dxf   # 只打开第一个，其余参数传给 Qt

真正的界面逻辑在 ``src/ilovecad/app.py``；这里只负责把 ``src/`` 放进
模块搜索路径，方便从仓库根目录、快捷方式或资源管理器直接启动。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ilovecad.app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
