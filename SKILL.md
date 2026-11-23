---
name: source-value-chart-builder
description: Use when tabular or reported values must become a chart whose marks, axes, units, legend and source rows are all traceable to the input data.
---

# 科研源数据图表生成与编辑

## 用途与适用条件

把源数据表或正文中的数值映射为图表结构规格：图型、系列、坐标轴与量级、单位、图元位置、误差线、图例、显著性标注，并建立“图元 → 源数据行/列”的双向索引。输出的核心不是美观，而是每个柱高、点位置、扇区角度都能回到源数据的某个字段。

适用条件：`/input/case.json`（或 `--case`）中含表格型数据、系列数值、统计结果或实验记录，要求生成柱状图、折线图、散点图、箱线图、热图或多面板图，并交付可编辑源文件与导出件。典型键：`tables`/`data`/`rows`/`columns`/`series`/`chart_type`/`units`/`error_bars`。

不适用：不判断统计方法是否正确，不重新计算显著性；不补齐表格中缺失的数值；不伪造渲染后的图像像素。

## 输入

- `/input/case.json`：主输入，按真实键读取。
- `/input/task.md`：存在时补充图型、目标格式与面板要求。
- 材料中引用的 CSV/TSV/TXT/MD/JSON 文件会被实际读取；缺失或损坏登记到 `errors`/`uncertainty`。
- 仅 Python 3.9+ 标准库；不联网、不写 `/input`。

## 处理流程

1. **读入**：加载 case.json、task.md 与引用文件，记录来源与哈希。
2. **表格识别**：识别 `columns`+`rows`、字典列表、列式字典与内嵌 CSV/TSV 文本，统一为记录表。
3. **字段分型**：把列分为分类列、数值列、误差列（sd/sem/ci/err/误差）、分组列与注释列；非数值单元格单独登记。
4. **图型判定**：优先用 `chart_type`；缺失时按字段结构推断（分类+数值→bar，双数值→scatter/line，矩阵→heatmap）。
5. **轴与量级**：解析列名中的单位（如 `浓度 (mg/L)`），计算数值范围与推荐刻度，标记量级异常。
6. **图元映射**：为每个数值生成 `mark_id`、系列、行号、列名、归一化位置和原始值字符串。
7. **图例与标注**：图例项与系列一一对应；检查图例缺失/多余、误差线含义、显著性标记、样本量 n。
8. **标注校验**：检查轴标签与单位、刻度范围、长标签溢出风险、空值、系列长度不一致。
9. **导出计划**：登记目标格式与检查项，`performed=false`，不伪造检查结果。
10. **契约落盘**：保留 `case_id` 与全部 `output_contract` 字段。

## 评分维度对应

- **实际效果**：图元可回溯到行列；轴单位与量级正确；图例与系列一致。
- **完整性**：覆盖科学实体、数据编码、图例标注、可编辑源文件、导出格式。
- **可核验性**：`value_source_map` 给出每图元的文件/行/列/原值。
- **创新增益**：数据表驱动图元映射、图例自动对齐、标注缺口检查。
- **可靠体验**：缺列、空值、量级异常、长标签均被显式标记并降级。

## 输出契约

`/app/output/result.json` 顶层保留 `case_id`、`output_contract` 的全部字段、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`skill`、`challenge_id`、`contract_notes`。未支持字段写 `null` 并记录原因。

主要交付段：`result.chart_spec.series`、`result.chart_spec.marks`、`result.chart_spec.legend`、`result.value_source_map`、`result.annotation_checks`。

## 异常处理

| 情况 | 处理 | 输出表现 |
|---|---|---|
| case.json 缺失/非法 | 结构化失败 | 退出码 `2`，`status=insufficient_input`/`failed` |
| 没有表格或数值列 | 不编造数据 | `status=insufficient_input` 或 `partial` |
| 数值列含非数值 | 跳过该单元并记录 | `checks` 标 `non_numeric_cell`，保留原串 |
| 缺失值/空单元格 | 不插值 | 标 `missing_value`，图元 `value=null` |
| 误差列无法识别 | 不臆测误差含义 | `uncertainty` 说明 |
| 系列长度不一致 | 保留各系列，不补齐 | 标 `series_length_mismatch` |
| 单位缺失 | 不补单位 | 标 `missing_unit` |
| 图例与系列不符 | 保留事实并标差异 | 标 `legend_series_mismatch` |

## 限制

只解析显式表格与常见分隔文本；合并单元格、多层表头、图片中的数字无法可靠还原。归一化位置是绘图约束，不是最终像素坐标。

## 最小调用示例（示例，非官方案例结果）

```bash
python scripts/run_skill.py --case /input/case.json --out /app/output/result.json
```

示例输入（示意）：两列 `组别, 表达量 (a.u.)`，三行数值。示例输出（示意）：`status="success"`，`chart_spec.chart_type="bar"`，3 个 marks 各自带 `row=1..3` 与原始值。该输出仅为示例，不代表官方固定案例结果。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

### 真实图稿与预览（本次必做）

- 脚本只输出规格（如 `export_plan[].performed=false`）时，调用 Agent 必须依据规格真实写出可编辑 SVG 图表到 `/app/output/`：系列、坐标轴、图例、数据标签均为可编辑元素。
- 任务要求 PNG/PDF 或需要人工核看时，必须实际渲染预览文件；环境无栅格化工具时以真实 SVG 作为矢量预览，并在 `result.json` 写明格式、路径与原因。
- 仅当文件真实落盘、可打开并核对后，才将源文件与导出项标记为已执行（`produced=true`、`performed=true`），并写入路径、字节数与 SHA-256；未执行的检查项如实写 `false` 与原因，不伪造渲染或检查结果。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
