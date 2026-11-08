# IloveCAD

一个面向 **2D 工程图纸** 的轻量级桌面 CAD 看图 + 轻度修改软件。基于 GNU
LibreDWG 做 DWG 读写内核，Qt for Python (PySide6) 做原生桌面界面。

> **纯净版承诺**：本项目**没有任何广告、没有会员 / VIP 等级、没有订阅、没有
> 账号体系、没有云盘依赖、没有遥测上报、没有功能付费墙**。源码里出现的每一个
> 功能，对每一个用户都无条件可用。请不要以任何形式向本项目的衍生发行版加入
> 上述内容——这与项目的许可和使用约定直接冲突。

> **本项目只处理 2D 图纸。3D 图纸支持被明确、永久地跳过。** 不带任何 3D 实体
> 渲染、3D 视图、三维导航、点云、网格或实体建模能力；遇到 3D 实体（3DSOLID、
> 3DFACE、REGION、SURFACE、MESH、ACIS 数据等）时，软件会在导入报告中明确列出
> 「已跳过」，不会假装显示、不会静默丢弃、也不会把它们写回文件。UI 中不存在
> 「视觉样式 / 三维视图 / 模型渲染」一类入口。这是设计决定，不是待办事项。

## 硬性架构约定

| 约定 | 落地方式 |
|---|---|
| 读写内核 | GNU LibreDWG（GPL-3.0-or-later），通过 `dwgread` / `dwgwrite` 调用，源码位于 `src/ilovecad/kernel/` |
| 写入锁定 R2004 | 唯一常量 `TARGET_VERSION = "AC1018"`；`dwgwrite --as r2004`；写出后校验文件头 == `AC1018` 并回读，失败即拒绝落盘，**无 R2000 静默回退** |
| 高版本读入 | R2007 / R2010 / R2013 / R2018 走**源头降版本兜底**：先在临时副本上转成 R2004 再进入文档模型，原文件永不改写，并生成导入报告 |
| PDF 输出 | **独立渲染路径**：从统一 DisplayList 直接送专用 PDF 绘制设备，不经过 DWG 写入、不截图、不调用 `dwg2ps` / `dwg2SVG`，与 DWG 版本闸门完全解耦 |
| 开源许可 | 全项目 `GPL-3.0-or-later`，每个源文件带 SPDX 头；二进制发行附对应源码，见 `THIRD_PARTY.md` |

## 功能

**看图**：打开 DWG（R13–R2004 直读，R2007–R2018 降版本兜底）与 ASCII DXF；
图层列表、灯泡开关、冻结、颜色 / 线型 / 线宽；平移、滚轮缩放、窗口缩放、
适应全图、网格、正交；坐标与比例尺状态栏。

**实体显示**：LINE、LWPOLYLINE / POLYLINE（含凸度圆弧）、CIRCLE、ARC、
ELLIPSE、POINT、TEXT / MTEXT、SOLID、INSERT 块参照展开、SPLINE 折线逼近；
HATCH / DIMENSION 做简化显示；其余在导入报告中计数说明。

**测量**：端点 / 中点 / 圆心 / 交点 / 最近点 / 垂足捕捉；两点距离及水平垂直
分量、角度、多段累计长度、闭合区域面积、半径 / 直径 / 弧长；单位换算与精度
设置；测量结果列表与画布临时标注。

**轻量编辑**：点选 / 窗选 / 多选；绘制直线、多段线、矩形、圆、圆弧、文字；
删除、移动、复制、旋转；改图层 / 颜色；撤销重做命令栈；保存 / 另存为
（**始终 AC1018**），保存前明确提示将丢失的不支持对象。

**输出**：矢量 PDF（A4 / A3 / A2、比例、当前视图 / 全图、彩色 / 黑白、
线宽映射、可含测量标注）。

## 界面

参考「CAD 迷你看图」与「浩辰 CAD 看图王」的六分区骨架重新绘制，但去掉了
账号、云盘、分享、会员、订阅与广告位：

```
┌────────────────────────────────────────────────────────────┐
│ 标题栏：文件名  │ 撤销/重做 │ 文件/视图/绘制/编辑/测量/输出 菜单 │
├────────────────────────────────────────────────────────────┤
│ 功能区（分组工具按钮）：文件 · 视图 · 绘制 · 编辑 · 测量 · 图层 · 输出 │
├──────────┬──────────────────────────────────┬──────────────┤
│ 图层面板  │                                  │ 属性面板      │
│ (灯泡/冻结)│        中央 2D 图纸画布            │ 测量结果      │
│          │                                  │ 导入报告      │
├──────────┴──────────────────────────────────┴──────────────┤
│ 命令提示栏：当前命令的下一步指引                              │
├────────────────────────────────────────────────────────────┤
│ 状态栏：坐标 X/Y │ 比例 │ 捕捉 │ 正交 │ 栅格 │ 线宽 │ AC1018 │
└────────────────────────────────────────────────────────────┘
```

## 目录结构

```
src/ilovecad/
  kernel/     LibreDWG 适配层：版本闸门、源头降版本、R2004 写入锁定、导入报告
  model/      文档模型：实体、图层、单位、几何计算
  render/     DisplayList + Qt 屏幕绘制 + 独立 PDF 绘制路径
  tools/      捕捉、测量、编辑命令与撤销重做栈
  ui/         Qt Designer .ui 源文件、画布控件、窗口控制器
  app.py      入口
scripts/
  fetch_libredwg.py      下载并校验锁定版本的 LibreDWG（含对应源码）
  compile_ui.py          把 .ui 编译为 Python
  run.py                 启动器
  make_source_bundle.py  生成合规的对应源码包
tests/                   单元测试与往返回归
```

## 快速开始（Windows / Linux / macOS）

```bash
python -m pip install -r requirements.txt
python scripts/fetch_libredwg.py          # 下载锁定的 LibreDWG 二进制 + 源码
python scripts/compile_ui.py              # 编译 .ui -> Python
python scripts/run.py                     # 启动
```

Linux / macOS 上没有官方预编译包时，`fetch_libredwg.py` 会打印源码构建命令；
按 `THIRD_PARTY.md` 用 `./configure && make` 构建即可，程序会自动探测
`dwgread` / `dwgwrite`。

## 修改界面（重要）

界面**全部定义在 Qt Designer 的 `.ui` XML 文件**里，可直接人工编辑：

```
src/ilovecad/ui/main_window.ui
src/ilovecad/ui/pdf_export.ui
src/ilovecad/ui/import_report.ui
src/ilovecad/ui/about.ui
```

用 `pyside6-designer` 打开修改，保存后执行 `python scripts/compile_ui.py`
重新编译，再重启程序即可生效。编译产物放在 `src/ilovecad/ui/generated/`
（不入库）。程序启动时会自动比对 `.ui` 与编译产物的时间戳，发现改动就自动
重编，所以改完直接启动也能立即预览。

## 自检

```bash
python scripts/selftest.py
# 可选：加入一张高版本图纸检查源头降版本
ILOVECAD_HIGH_VERSION_SAMPLE=/path/to/example_2018.dwg python scripts/selftest.py
```

自检会串起完整链路：文档模型 → AC1018 DXF → `dwgwrite --as r2004` → 文件头
校验 → 回读实体数与类型一致 → DisplayList → 独立矢量 PDF → 离屏主窗口截图 →
高版本源头降版本。脚本默认用 Qt 的 `offscreen` 平台以便无桌面运行；该平台在
Windows 上不注册系统字体，截图文字会显示为方框，真实 `windows` 平台显示正常
（可用 `QT_QPA_PLATFORM=windows` 覆盖）。

## 版本策略

* 文件头 `AC1015` / `AC1018`（R2000 / R2004）→ 直接解码。
* `AC1021` / `AC1024` / `AC1027` / `AC1032`（R2007 / 2010 / 2013 / 2018）→
  **源头降版本**：`dwgread -O DXF` → `dwgwrite --as r2004` 生成临时低版本
  DWG → 再解码，全过程在 LibreDWG 内完成，不捆绑任何专有转换器。
* 用户可在设置里填写**自备**的外部转换器路径作为最后一级可选钩子；该组件
  既不随发行版分发，也不是必需依赖。
* 保存永远输出 `AC1018`；写出后在临时文件上校验文件头与回读结果，通过后才
  原子替换目标文件。校验失败保留原文件并写日志。

## 已知限制

* **不支持 3D 图纸**（见开头说明），也不支持参数化约束、动态块、代理对象、
  外部参照、天正自定义实体的完整语义。
* LibreDWG 的 R2004 编码器仍是 beta；本项目固定含 0.14.1 安全修复的 nightly，
  并对每次写入做回读校验。
* SHX 字体不做完整字库，中文 / 特殊符号以系统字体近似替代，缺失时在导入
  报告中列出。
* 保存时会用**结构完整的 AC1018 DXF 骨架**重建模型（含 `BLOCK_RECORD` 表、
  唯一句柄与 `330` owner 引用——LibreDWG 的 DXF importer 缺少这些会静默丢弃
  全部实体），因此原图的 XDATA / 代理对象 / 复杂块定义不会原样回写；保存前
  会明确列出将丢失的对象并需要确认，**不会静默丢数据**。

## 许可

`GPL-3.0-or-later`，完整文本见 `COPYING`，声明见 `NOTICE`，第三方许可矩阵见
`THIRD_PARTY.md`。修改并分发时，请保留 SPDX 头、提供对应源码、不得添加任何
附加限制（包括广告、会员、订阅或功能付费墙）。

## 仓库

公开仓库：<https://github.com/pxhhero-bit/ilovecad>（`main` 分支，`origin`）。
本项目不提交 LibreDWG 二进制，克隆后按「快速开始」执行
`python scripts/fetch_libredwg.py` 获取锁定版本（校验值见
`third_party/libredwg/PIN.json`）。
