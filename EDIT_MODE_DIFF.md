# 只读 / 编辑模式差异比对

图纸：`台秤方案.dwg`（AC1032），由 `scripts/compare_edit_modes.py` 实际打开同一图纸、确认降级后采集。

差异项合计：**9**；分类统计：`{'expected_state': 9}`。

## 1. 模式切换本身的差异

- 文档模型哈希一致：**True**
- DisplayList 文字哈希一致：**True**
- 画布逐像素一致：**True**（比对时屏蔽了跟随鼠标的十字准线，否则模态对话框会移动光标，产生一条 6 px 的假差异）
- 视图 scale / 中心、图层显隐、文字样式、跳过对象、导入 DXF 快照均一致。

结论：**只读 → 编辑切换不重建文档、不改变任何显示数据，显示层面 0 项需要校正**。下列 9 项都是预期的会话 / UI 状态变化。

| 字段 | 只读 | 编辑 | 分类 |
|---|---|---|---|
| `canvas.read_only` | True | False | expected_state |
| `report.text_sha256` | 1c0d4c45b233bd31 | ab1a13752c8a041a | expected_state |
| `session.degraded` | False | True | expected_state |
| `session.edit_started_at` |  | 2026-11-10T01:57:52 | expected_state |
| `session.mode` | readonly | editable | expected_state |
| `session.read_only` | True | False | expected_state |
| `session.risk_snapshot_lines` | 0 | 13 | expected_state |
| `status_mode` | 模式：只读 | 模式：编辑·已降级 | expected_state |
| `title` | 台秤方案.dwg [只读] — IloveCAD 0.1.0 · R2004 / AC1018 · 2D ONLY | 台秤方案.dwg [编辑·已降级] — IloveCAD 0.1.0 · R2004 / AC1018 · 2D ONL | expected_state |

## 2. 两种写出路径

| 项目 | 只读「原样副本」 | 编辑「保存 / 另存为」 |
|---|---|---|
| 实现 | 源文件按字节复制 | 2D 模型重建为 AC1018 DXF → dwgwrite |
| 输出字节 | 1,164,651 | 460,978 |
| 文件头 | AC1032 | AC1018 |
| 与原文件字节一致 | True | 否 |
| 实体数 | 5,976（原图） | 6,009 |
| 文字样式 | 18 | 18 |
| 图层 | 57 | 57 |
| 源文件 SHA-256 不变 | 是 | 是 |

实体类型变化（编辑写出后）：

- ARC: 153 → 153
- CIRCLE: 35 → 35
- ELLIPSE: 41 → 41
- HATCH: 48 → 0
- LINE: 3765 → 3765
- LWPOLYLINE: 1105 → 1890
- POINT: 13 → 59
- SPLINE: 756 → 0
- TEXT: 60 → 66

## 3. 差异分类与可校正性

### A. 显示 / 会话差异：需校正 0 项

模型、DisplayList、画布像素、视图、图层与样式在切换前后完全一致；9 项差异全部是模式标志、时间戳、风险快照、标题 / 状态栏 / 报告文本等预期状态，无需也不应校正。

### B. 编辑模块行为差异：已直接校正 4 项（实测）

1. **未产生编辑历史时的「另存为」改为无损字节副本**：实测 `unedited_saveas_lossless = True`。
2. **`READONLY` 放弃编辑回到只读**：实测 `{'mode_readonly': True, 'not_degraded': True, 'document_identical': True, 'display_list_identical': True, 'canvas_pixels_identical': True, 'source_snapshot_unchanged': True}`。
3. **多行 MTEXT 按 `\P` 分行写出**：本次重建共写出 7 行文字、6,009 个 DXF 实体（回读实体数同步计入，不再出现字面量 `\P`）。
4. **保存确认框 / 报告的近似统计直接由 `write_dxf` 的 `stats` 生成**（SPLINE 已按 NURBS 求值折线，不再误写「控制点折线」）。

### C. 需要用户显式确认

- 进入编辑是一次性降级：R2004 重建会丢失 XDATA / 代理对象 / 复杂块语义，必须由用户确认风险清单。
- 覆盖同名目标文件、切换单位标记、丢弃未保存的编辑去打开新图。

### D. 架构硬约束，不可校正

- 写入锁定 R2004 / AC1018，无 R2000 回退。
- 只处理 2D：3D 实体、代理对象、外部参照、SHX 字库不分发。
- SPLINE / HATCH / DIMENSION 只能 2D 近似写出；本次重建中 SPLINE 756→0、HATCH 48→0，转为 LWPOLYLINE 与 POINT。
- 源文件在任何模式下都不得被覆盖。
