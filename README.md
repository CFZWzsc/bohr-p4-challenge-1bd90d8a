# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-1bd90d8a` (track: `scientific-graphics`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `source-value-chart-builder`
- Summary: Use when tabular or reported values must become a chart whose marks, axes, units, legend and source rows are all traceable to the input data.

## Contents

| File |
|------|
| `SKILL.md` |
| `examples/demo-case/case.json` |
| `examples/demo-case/expression.csv` |
| `examples/demo-case/material.md` |
| `examples/demo-case/task.md` |
| `scripts/figure_svg.py` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

A single deterministic pass reads `/input`, computes the deliverable and writes a
contract-complete `/app/output/result.json` plus the human-readable review files
described in `SKILL.md`. The same input always produces byte-identical output; no
network access is used, and fields the materials cannot support are reported
explicitly instead of being left empty.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
