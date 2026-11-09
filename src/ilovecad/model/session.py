# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only source layer versus the editable 2D model layer.

Opening a drawing produces a :class:`DocumentSession` in ``readonly`` mode:
the original file bytes are never touched (verified by SHA-256), the pristine
DXF produced by the importer is kept as an in-memory snapshot, and the parsed
2D model is used for *viewing only*.  Editing is an explicit step: after the
user accepts the risk list, :meth:`DocumentSession.enter_edit` degrades the
session to the editable 2D model (DXF re-serialisation -> dwgwrite --as r2004
-> read-back gate).  The degradation is reversible **in memory**: the pristine
import snapshot is still there, so :meth:`restore_readonly` can discard the
edits and rebuild a fresh read-only model without ever touching the source.

The source layer is deliberately kept separate from ``DwgDocument``: the
latter is an approximation (XDATA, proxy objects and complex blocks do not
survive DXF round-tripping), so it must never be mistaken for the source.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .document import DwgDocument

if TYPE_CHECKING:                       # avoid a kernel <-> model import cycle
    from ..kernel.core import ImportReport

READONLY = "readonly"
EDITABLE = "editable"

APPROXIMATED = ("SPLINE", "HATCH", "MTEXT", "DIMENSION", "INSERT")


def sha256_file(path: str | Path) -> str:
    """Streaming SHA-256, so a 200 MB drawing is never loaded twice."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class DocumentSession:
    source_path: Path | None
    source_sha256: str
    source_format: str                  # "dwg" | "dxf"
    source_version: str
    import_dxf: bytes                   # pristine importer output, view-layer snapshot
    document: DwgDocument
    report: Any = None                  # ImportReport (typed only under TYPE_CHECKING)
    mode: str = READONLY
    degraded: bool = False
    edit_started_at: str = ""
    risk_snapshot: list[str] = field(default_factory=list)

    @property
    def read_only(self) -> bool:
        return self.mode == READONLY

    @property
    def source_name(self) -> str:
        return self.source_path.name if self.source_path else "未命名图纸"

    def enter_edit(self) -> list[str]:
        """Degrade the read-only session into the editable 2D model."""
        self.risk_snapshot = self.risk_lines()
        self.mode = EDITABLE
        self.degraded = True
        self.edit_started_at = datetime.now().isoformat(timespec="seconds")
        return self.risk_snapshot

    def restore_readonly(self, document: DwgDocument) -> None:
        """Discard all in-memory edits and return to a fresh read-only model.

        ``document`` must be rebuilt from ``self.import_dxf`` (never from the
        source file), so the pristine snapshot stays the single source of
        truth and the source bytes are not read or verified again.
        """
        self.document = document
        self.mode = READONLY
        self.degraded = False
        self.edit_started_at = ""
        self.risk_snapshot = []

    # ------------------------------------------------------------------ risks
    def risk_lines(self) -> list[str]:
        doc = self.document
        skipped = dict(doc.skipped or {})
        counts = Counter(e.kind for e in doc.entities)
        lines = [
            f"源文件：{self.source_name}（{self.source_format.upper()}，"
            f"版本 {self.source_version or '未知'}）",
            f"源文件 SHA-256：{self.source_sha256[:16]}…（打开时记录，"
            "写入前会重新核对）",
            f"导入快照：{len(self.import_dxf):,} 字节 DXF，完整保留在内存中，"
            "只读期间不参与任何写出",
            f"当前 2D 模型：{len(doc.entities):,} 个实体，"
            f"{len(doc.layers)} 个图层",
        ]
        if self.report is not None and getattr(self.report, "downgraded", False):
            lines.append(
                f"源头降版本：源 {self.source_version or '未知'} 已在临时副本上"
                "降为 AC1018 后读入；原文件从未改写")
        if skipped:
            total = sum(skipped.values())
            lines.append(f"导入时明确跳过 {total} 个对象（不会显示也不会写回）："
                         + "、".join(f"{k} × {v}" for k, v in
                                    sorted(skipped.items())[:10]))
        approx = [(k, counts[k]) for k in APPROXIMATED if counts.get(k)]
        if approx:
            lines.append("进入编辑后按 2D 近似类型处理、几何可能失真："
                         + "、".join(f"{k} × {v}" for k, v in approx))
            if counts.get("SPLINE"):
                lines.append("SPLINE 已按 NURBS 求值，写回仍为 2D 折线近似；"
                             "HATCH 填充图案与 MTEXT 内联格式语义同样不还原")
            lines.append("文字按 STYLE 表 / 内联字型映射到本机字体显示，SHX 字库"
                         "不随软件分发；写回保留源字高、宽度因子与 MTEXT 分行")
        lines += [
            "经 DXF 中转，XDATA、代理对象、复杂块定义与 3D 实体不会原样回写",
            "编辑只作用于内存中的 2D 文档模型；保存/另存为始终锁定 "
            "R2004 / AC1018，并做文件头 + 回读实体数双重校验",
            "只读期间可用「另存原样副本」按字节复制源文件（无损、不重建）；"
            "进入编辑后该选项不再代表当前内存模型",
            "可用 READONLY / ABANDON 放弃编辑，从导入 DXF 快照恢复只读"
            "（源文件不受影响）",
        ]
        if getattr(doc, "source_units", None) == 0:
            lines.append("源图纸 $INSUNITS=0（无单位）：写出保留该标记，"
                         "测量数值仅按本机显示单位解释")
        return lines

    def mode_text(self) -> str:
        if self.source_path is None:
            return "可编辑（空白图纸）"
        if self.read_only:
            return "只读"
        return "编辑·已降级"

    def summary_text(self) -> str:
        lines = [
            "",
            "============ 会话模式 ============",
            f"当前模式：{self.mode_text()}",
            f"源文件：{self.source_path}" if self.source_path else "源文件：无",
            f"源文件 SHA-256：{self.source_sha256}",
            f"只读层导入快照：{len(self.import_dxf):,} 字节（未经修改）",
        ]
        if self.degraded:
            lines += [f"降级时间：{self.edit_started_at}",
                      "当前状态：编辑模式（2D 可编辑模型，已降级）",
                      "放弃编辑可输入 READONLY 恢复只读（源文件不受影响）",
                      "已接受的风险："]
            lines += [f"  · {line}" for line in
                      (self.risk_snapshot or self.risk_lines())]
        else:
            lines += [
                "只读：可进行看图、图层显隐、对象捕捉、测量与矢量 PDF 导出；"
                "所有写入被拒绝。",
                "如需编辑，点「进入编辑」或输入 EDIT，确认风险后降级为 "
                "2D 可编辑模型。",
            ]
        return "\n".join(lines)


def empty_session(doc: DwgDocument | None = None) -> DocumentSession:
    """Session for a blank, unsaved drawing: editable from the start."""
    return DocumentSession(
        source_path=None, source_sha256="", source_format="", source_version="",
        import_dxf=b"", document=doc or DwgDocument(), report=None,
        mode=EDITABLE)
