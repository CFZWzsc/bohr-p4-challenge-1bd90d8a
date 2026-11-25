# 科研数据图表生成与编辑 Skill

把材料里的表格/内联数值画成带坐标轴、刻度、单位、误差线与来源标注的数据图，
并导出 300 dpi 的 `figure.png` 与 `figure.pdf`。Python 标准库实现，离线、确定性。

## 使用

```bash
python scripts/run_skill.py --case examples/demo-case/case.json \
    --out /app/output/result.json --work /app/work
```

- 输入：`case.json`、`task.md`、含表格的 CSV 或正文（`examples/demo-case/expression.csv` 为样例）。
- 输出：`result.json`（契约键 `case_id`/`status`/`editable_source`/`export_artifact`/
  `content_map`/`compliance_checks`/`exceptions`）、`figure.svg`、`figure-spec.json`、
  `figure.png`、`figure.pdf`。

## 设计要点

- **数据可回溯**：每个绘制值带 `value_origin`（列/行或原文片段），`evidence` 记录解析来源；
  单位缺失或数值歧义写入 `uncertainty`，不猜测。
- **图面规范**：坐标轴、刻度值、轴标题（含单位）、组标签、误差线、图例与标题齐全；
  数值标签不重复拼接单位。
- **可编辑**：`layer-axes` / `layer-series` / `layer-labels` / `legend` 分层，文字全部 `<text>`。
- **可核验**：`compliance_checks` 覆盖数值来源、轴范围包含数据、刻度数量、图例、PNG 非空白像素。
- **确定性**：解析与布局只依赖输入顺序，无随机数与时间戳，双跑逐字节一致。

## 验证

```bash
python scripts/run_skill.py --case examples/demo-case/case.json --out /tmp/r1/result.json --work /tmp/r1
python scripts/run_skill.py --case examples/demo-case/case.json --out /tmp/r2/result.json --work /tmp/r2
# 两次产物 sha256 应完全一致
```
