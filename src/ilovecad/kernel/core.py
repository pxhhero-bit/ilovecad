# SPDX-License-Identifier: GPL-3.0-or-later
"""LibreDWG CLI adapter, source-side downgrade fallback and R2004 write lock.

This is the only module that talks to the LibreDWG executables. All version
policy lives here, driven by the single constant TARGET_VERSION.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..model.document import DwgDocument
from ..model.session import (DocumentSession, READONLY, sha256_file)
from ..version import (MAX_DIRECT_READ_VERSION, TARGET_VERSION,
                       TARGET_VERSION_FLAG, VERSION_LABELS, VERSION_ORDER)
from .dxf_codec import normalize_newlines, parse_dxf, write_dxf
from .native_text import (apply_native_text, legacy_mtext_dialect,
                          load_native_text)

ROOT = Path(__file__).resolve().parents[3]
TP = ROOT / "third_party" / "libredwg"
MAX_FILE_BYTES = 200 * 1024 * 1024
PROCESS_TIMEOUT = 180


class KernelError(RuntimeError):
    pass


@dataclass
class ImportReport:
    path: str = ""
    source_version: str = ""
    effective_version: str = ""
    downgraded: bool = False
    steps: list[str] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    layers: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        v = lambda x: f"{x} ({VERSION_LABELS.get(x, '未知')})" if x else "未知"
        lines = [
            "IloveCAD 导入报告",
            "====================",
            f"文件：{self.path}",
            f"源文件版本：{v(self.source_version)}",
            f"实际解码版本：{v(self.effective_version)}",
            f"源头降版本：{'是，已在临时副本上转为 R2004 后读入' if self.downgraded else '否，直接读入'}",
            "",
            "处理步骤：",
        ]
        lines += [f"  {i + 1}. {s}" for i, s in enumerate(self.steps)] or ["  （无）"]
        lines += ["", f"图层数：{self.layers}"]
        lines += [f"实体总数：{self.counts.get('entities', 0)}"]
        if self.counts.get("by_kind"):
            items = sorted(self.counts["by_kind"].items(), key=lambda kv: -kv[1])[:18]
            lines.append("实体类型统计：" + "、".join(f"{k}×{n}" for k, n in items))
        lines += ["", "明确跳过的内容："]
        if self.skipped:
            for k, n in sorted(self.skipped.items(), key=lambda kv: -kv[1]):
                tag = "【3D，按设计跳过】" if k.startswith("3D:") else "【不支持】"
                lines.append(f"  {tag} {k} × {n}")
        else:
            lines.append("  （无）")
        lines += ["", "说明与已知降级："]
        lines += [f"  - {n}" for n in self.notes] or ["  （无）"]
        lines += ["  - 本项目仅支持 2D 图纸，3D 实体不会显示也不会写回。"]
        lines += ["  - 经 DXF 中转，XDATA / 代理对象 / 复杂块语义可能丢失；保存前会再次提示。"]
        if self.warnings:
            lines += ["", "警告："] + [f"  - {w}" for w in self.warnings]
        lines += ["", "许可：GPL-3.0-or-later；内核 GNU LibreDWG（GPL-3.0-or-later）。",
                  "无广告 · 无会员 · 无订阅 · 无账号 · 无遥测"]
        return "\n".join(lines)


class LibreDwgKernel:
    """The DWG read/write kernel."""

    def __init__(self, root: Path | None = None):
        self.root = root or ROOT
        tp = self.root / "third_party" / "libredwg"
        self.tools: dict[str, str] = {}
        tools_file = tp / "tools.json"
        if tools_file.exists():
            try:
                self.tools = json.loads(tools_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        bin_dir = tp / "bin"
        for name in ("dwgread", "dwgwrite", "dwg2dxf", "dxf2dwg", "dwgrewrite"):
            if name not in self.tools or not Path(self.tools[name]).exists():
                for suffix in (".exe", ""):
                    candidate = bin_dir / (name + suffix)
                    if candidate.exists():
                        self.tools[name] = str(candidate)
                        break
                else:
                    found = shutil.which(name)
                    if found:
                        self.tools[name] = found
        self.external_converter = os.environ.get("ILOVECAD_EXTERNAL_CONVERTER", "").strip()

    # ------------------------------------------------------------------ helpers
    def _tool(self, name: str) -> str:
        path = self.tools.get(name)
        if not path or not Path(path).exists():
            raise KernelError(
                f"找不到 LibreDWG 工具 {name}。请先运行 python scripts/fetch_libredwg.py，"
                "或在 Linux/macOS 上从源码构建 LibreDWG >= 0.14.1。")
        return path

    def _run(self, name: str, args: list[str], timeout: int = PROCESS_TIMEOUT,
             allow_log_errors: bool = False) -> str:
        cmd = [self._tool(name)] + [str(a) for a in args]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                  creationflags=flags, check=False)
        except subprocess.TimeoutExpired as exc:
            raise KernelError(f"{name} 超时（{timeout}s），已终止；文件可能损坏或过大。") from exc
        out = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
        # dwg2dxf reports non-fatal proxy-object "ERROR:" lines on real R2018
        # sheets while still writing a complete DXF and exiting 0.  Only the
        # produced file is authoritative for that tool.
        if proc.returncode != 0 or (not allow_log_errors and
                                    "SUCCESS" not in out and "ERROR" in out):
            raise KernelError(f"{name} 执行失败：\n{out.strip()[-1200:]}")
        return out

    @staticmethod
    def detect_version(path: Path) -> str:
        with path.open("rb") as fp:
            head = fp.read(6)
        try:
            s = head.decode("ascii").strip().upper()
        except UnicodeDecodeError:
            return ""
        return s if re.fullmatch(r"AC\d{4}", s) else ""

    @staticmethod
    def _version_index(v: str) -> int:
        return VERSION_ORDER.index(v) if v in VERSION_ORDER else -1

    def _read_to_dxf(self, source: Path, dest: Path) -> None:
        """DWG -> ASCII DXF using the full-fidelity ``dwg2dxf`` writer.

        ``dwgread -O DXF`` uses a legacy MTEXT dialect where group 40 is the
        reference rectangle width instead of the text height.  Rendering that
        value makes title-block labels tens of times too large and shifts them
        out of their cells.  ``dwg2dxf`` emits the standard DXF group codes.
        """
        try:
            self._run("dwg2dxf", ["-y", "-o", str(dest), str(source)],
                      allow_log_errors=True)
        except KernelError:
            # Older / self-built LibreDWG packages may lack dwg2dxf.  The
            # legacy output is still usable; native_text.py repairs MTEXT.
            self._run("dwgread", ["-O", "DXF", "-o", str(dest), str(source)])
        if not dest.exists() or dest.stat().st_size == 0:
            raise KernelError("转换器未产生 DXF 数据")
        data = normalize_newlines(dest.read_bytes())
        if b"EOF" not in data[-4096:]:
            raise KernelError("DXF 输出不完整（缺少 EOF 结束标记）")
        dest.write_bytes(data)

    def _downgrade(self, source: Path, work: Path) -> Path:
        """Source-side downgrade: high-version DWG -> DXF -> R2004 DWG."""
        dxf = work / "downgrade.dxf"
        low = work / "downgraded_r2004.dwg"
        self._read_to_dxf(source, dxf)
        self._run("dwgwrite", ["--as", TARGET_VERSION_FLAG, "-I", "DXF",
                              "-o", str(low), "-y", str(dxf)])
        header = self.detect_version(low)
        if header != TARGET_VERSION:
            raise KernelError(f"源头降版本失败：期望 {TARGET_VERSION}，实际 {header or '无法识别'}")
        return low

    def _external_convert(self, source: Path, work: Path) -> Path | None:
        if not self.external_converter:
            return None
        out = work / "external_r2004.dwg"
        try:
            subprocess.run([self.external_converter, str(source), str(out)],
                           capture_output=True, timeout=PROCESS_TIMEOUT, check=True)
        except Exception:
            return None
        return out if out.exists() and self.detect_version(out) == TARGET_VERSION else None

    # --------------------------------------------------------------------- read
    def read(self, path: str | Path) -> tuple[DwgDocument, ImportReport]:
        """Compatibility API: the (document, report) pair of a read-only session.

        Batch conversion and self-tests use this. The main window uses
        :meth:`read_session`, which also keeps the untouched source layer.
        """
        session = self.read_session(path)
        return session.document, session.report

    def read_session(self, path: str | Path) -> DocumentSession:
        """Import a drawing into a **read-only** session.

        The original bytes are hashed and left untouched; the complete DXF
        emitted by the importer is retained as a snapshot; only the parsed 2D
        model is used for viewing.  Editing requires an explicit degradation
        step in the UI (see ``model.session.DocumentSession.enter_edit``).
        """
        source = Path(path)
        if not source.exists():
            raise KernelError(f"文件不存在：{source}")
        if source.stat().st_size > MAX_FILE_BYTES:
            raise KernelError("文件超过 200 MB 上限，已拒绝处理")
        source_sha256 = sha256_file(source)
        report = ImportReport(path=str(source))
        source_version = self.detect_version(source)
        report.source_version = source_version
        is_dxf = source.suffix.lower() in (".dxf", ".dxfb")
        import_dxf = b""

        with tempfile.TemporaryDirectory(prefix="ilovecad_") as td:
            work = Path(td)
            if is_dxf:
                report.steps.append("识别为 DXF，直接由内部 DXF 编解码器解析")
                import_dxf = normalize_newlines(source.read_bytes())
                report.effective_version = source_version or "DXF"
                doc, parsed = parse_dxf(import_dxf)
                report.steps.append("DXF 分组码解析完成")
            else:
                need_downgrade = (source_version and
                                  self._version_index(source_version) >
                                  self._version_index(MAX_DIRECT_READ_VERSION))
                dxf = work / "final.dxf"
                direct_error = None
                try:
                    self._read_to_dxf(source, dxf)
                except KernelError as exc:
                    direct_error = str(exc)

                if direct_error and need_downgrade:
                    report.steps.append(
                        f"检测到高版本 {source_version}，直接解码失败，触发源头降版本兜底")
                    try:
                        low = self._downgrade(source, work)
                        self._read_to_dxf(low, dxf)
                        direct_error = None
                        report.downgraded = True
                        report.effective_version = TARGET_VERSION
                        report.steps.append("dwg2dxf → dwgwrite --as r2004 → dwg2dxf")
                        report.steps.append("临时副本文件头已校验为 AC1018，原文件未被改写")
                    except KernelError as exc:
                        converted = self._external_convert(source, work)
                        if converted is None:
                            raise KernelError(
                                f"高版本 {source_version} 的源头降版本失败：{exc}\n"
                                "可自备外部转换器并通过环境变量 ILOVECAD_EXTERNAL_CONVERTER 指定。")
                        self._read_to_dxf(converted, dxf)
                        direct_error = None
                        report.downgraded = True
                        report.effective_version = TARGET_VERSION
                        report.steps.append("已使用用户自备的外部转换器降版本（未随本软件分发）")
                        report.warnings.append("使用了外部转换器，请自行核对其许可与输出精度")
                elif direct_error:
                    raise KernelError(f"LibreDWG 无法解码该文件：\n{direct_error}")
                else:
                    report.steps.append(
                        f"版本 {source_version or '未知'} 由 dwg2dxf 直接解码为完整 DXF"
                        "（MTEXT 字高 / 附着点为标准分组码）")
                    report.effective_version = source_version or TARGET_VERSION

                import_dxf = dxf.read_bytes()
                doc, parsed = parse_dxf(import_dxf)
                report.steps.append("DXF 分组码解析为 2D 文档模型")
                if legacy_mtext_dialect(import_dxf):
                    # The legacy MTEXT dialect (or the downgrade fallback that
                    # rebuilt the DWG from it) loses the true text height.
                    # Join native DWG fields back by handle so the viewer does
                    # not invent a display-only font size.
                    native = load_native_text(source, work / "native.json",
                                              self._run)
                    fixed = apply_native_text(doc, native)
                    if fixed:
                        from ..model.titleblock import apply_title_block_alignment
                        apply_title_block_alignment(doc, doc.align_title_text)
                        report.steps.append(
                            f"已从 DWG 原生字段恢复 {fixed} 个 MTEXT 的真实字高与附着点")
                    else:
                        report.warnings.append(
                            "检测到旧式 MTEXT 分组码，但未能从 DWG 原生字段恢复字高；"
                            "标题文字大小可能不准")

            doc.source_path = str(source)
            doc.source_version = source_version
            doc.effective_version = report.effective_version
            doc.downgraded = report.downgraded
            report.skipped = parsed.get("skipped", {})
            report.notes = parsed.get("notes", [])
            report.warnings.extend(parsed.get("warnings", []))
            report.layers = parsed.get("layers", 0)
            report.counts = {"entities": parsed.get("entities", 0),
                             "by_kind": parsed.get("by_kind", {})}
            if not doc.entities:
                report.warnings.append("未解析到任何可显示的 2D 实体，请查看跳过明细")
            if report.skipped:
                report.notes.append(
                    f"共 {sum(report.skipped.values())} 个对象未进入 2D 模型；保存时不会写回")
            session = DocumentSession(
                source_path=source, source_sha256=source_sha256,
                source_format="dxf" if is_dxf else "dwg",
                source_version=source_version, import_dxf=import_dxf,
                document=doc, report=report, mode=READONLY)
            report.steps.append(
                "已建立只读会话：源文件哈希登记、导入 DXF 快照保留，"
                "2D 模型仅用于看图")
            report.notes.append(
                "本次打开为只读；需编辑时请输入 EDIT 或点「进入编辑」，"
                "确认降级风险后才启用写入管线")
            return session

    # ---------------------------------------------------------- lossless copy
    def copy_source(self, session: DocumentSession,
                    target: str | Path) -> ImportReport:
        """Read-only 'save a copy': byte-for-byte copy of the original file.

        Unlike :meth:`write`, this never parses, re-serialises or downgrades
        anything, so XDATA / proxy objects / high-version features survive.
        """
        src = session.source_path
        dest = Path(target)
        if src is None or not src.exists():
            raise KernelError("没有可用的源文件，无法另存原样副本")
        if dest.resolve() == src.resolve():
            raise KernelError("不能覆盖当前打开的源文件，请换一个文件名或目录。")
        suffix = src.suffix.lower()
        if dest.suffix.lower() != suffix:
            raise KernelError(
                f"原样副本必须保持源格式 {suffix}；当前目标为 "
                f"{dest.suffix or '无扩展名'}。需要格式转换请使用批量转换。")
        current = sha256_file(src)
        if session.source_sha256 and current != session.source_sha256:
            raise KernelError(
                "源文件在打开之后被外部程序修改，已拒绝复制。"
                "请重新打开图纸以刷新只读会话。")
        report = ImportReport(path=str(dest),
                              source_version=session.source_version,
                              effective_version=session.source_version)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".ilovecad_copy_", suffix=suffix,
                                        dir=str(dest.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            shutil.copyfile(src, tmp)
            if sha256_file(tmp) != current:
                raise KernelError("副本校验失败：SHA-256 与源文件不一致，已放弃落盘")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        report.steps = [
            "只读会话：源文件按字节复制，未经 DXF 重建或版本转换",
            f"复制前后 SHA-256 校验一致：{current[:16]}…",
            "原子替换目标文件；源文件从未改写",
        ]
        report.notes = ["这是无损原样副本，包含源文件的全部数据（含 XDATA / 代理对象）。",
                        "当前内存中的 2D 模型不参与此操作。"]
        report.counts = {"entities": len(session.document.entities)}
        return report

    # -------------------------------------------------------------------- write
    def write(self, doc: DwgDocument, target: str | Path) -> ImportReport:
        """Write the document as R2004 (AC1018) DWG. No fallback version exists."""
        dest = Path(target)
        report = ImportReport(path=str(dest), source_version=TARGET_VERSION,
                              effective_version=TARGET_VERSION)
        # 临时目录必须与目标文件同盘（Windows 不允许跨盘 os.replace）。
        dest.parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".ilovecad_write_", dir=dest.parent))
        try:
            dxf = work / "model.dxf"
            out = work / "out.dwg"
            stats: dict = {}
            dxf.write_bytes(write_dxf(doc, stats))
            report.steps.append("文档模型序列化为 AC1018 ASCII DXF（UTF-8 / \\U+ 转义）")
            self._run("dwgwrite", ["--as", TARGET_VERSION_FLAG, "-I", "DXF",
                                  "-o", str(out), "-y", str(dxf)])
            report.steps.append(f"dwgwrite --as {TARGET_VERSION_FLAG} 写出临时 DWG")
            header = self.detect_version(out)
            if header != TARGET_VERSION:
                raise KernelError(
                    f"版本闸门拒绝落盘：期望文件头 {TARGET_VERSION}，实际 {header or '无法识别'}。"
                    "不会产生 R2000 或其他降级文件，原文件保持不变。")
            report.steps.append("文件头校验通过：AC1018")
            verify = work / "verify.dxf"
            self._read_to_dxf(out, verify)
            vdoc, _ = parse_dxf(verify.read_bytes())
            expected = int(stats.get("written", len(doc.entities)))
            # A round-trip that merely "parses something" is not a validation:
            # LibreDWG used to silently drop entities with a broken skeleton.
            if len(vdoc.entities) != expected:
                raise KernelError(
                    f"回读校验失败：应写出 {expected} 个 2D 实体，"
                    f"实际回读 {len(vdoc.entities)} 个。已拒绝落盘，原文件保持不变。")
            report.steps.append(f"回读校验通过：{expected} / {expected} 个 2D 实体")
            if stats.get("converted"):
                report.notes.append("以下对象按 2D 近似类型写出（几何可能失真）：" +
                                    "、".join(f"{k} × {n}" for k, n in
                                             sorted(stats["converted"].items())))
            if stats.get("point_fallback"):
                report.notes.append(
                    f"{stats['point_fallback']} 个退化填充边界已按 POINT 保留位置")
            if stats.get("dropped"):
                report.notes.append(
                    f"{stats['dropped']} 个无有效顶点的空对象未写出（已在回读校验中计入）")
            if stats.get("skipped"):
                detail = "、".join(f"{k} × {v}" for k, v in
                                  sorted(stats["skipped"].items()))
                raise KernelError(
                    f"保存被拒绝：存在无法写入 R2004 的实体类型（{detail}）。"
                    "为避免静默丢失几何，已放弃落盘，原文件保持不变。")
            os.replace(out, dest)
            report.steps.append("原子替换目标文件，写入完成")
            report.counts = {"entities": len(doc.entities), "written": expected}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return report

    def export_dxf(self, doc: DwgDocument, target: str | Path) -> None:
        Path(target).write_bytes(write_dxf(doc))
