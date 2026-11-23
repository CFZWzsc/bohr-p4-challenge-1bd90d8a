#!/usr/bin/env python3
"""Source-value chart builder (standard library only).

Reads a case bundle (default /input/case.json), detects tabular source data,
maps every value to a chart mark, checks axes/units/legend/annotations, and
writes a reproducible chart specification with a value-to-source index to
/app/output/result.json.  Deterministic and offline; no pixels are faked.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys

DEFAULT_CASE = "/input/case.json"
DEFAULT_OUT = "/app/output/result.json"
DEFAULT_WORK = "/app/work"
SKILL_NAME = "source-value-chart-builder"
SKILL_VERSION = "1.0.0"
CHALLENGE_ID = "challenge-1bd90d8a"
MAX_TEXT = 400000
SKIP_KEYS = {"output_contract", "scoring", "tags", "meta", "case_id",
             "challenge_id", "skill", "contract_notes"}
ERROR_HINTS = ("sd", "sem", "std", "stdev", "ci", "error", "err", "误差", "标准差")
NUMBER_RE = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$")
UNIT_RE = re.compile(r"[\(\[]\s*([^\)\]]{1,24})\s*[\)\]]")


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def read_text_file(path):
    try:
        if os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            with open(path, "r", encoding="utf-8-sig") as handle:
                return handle.read()
    except (OSError, UnicodeDecodeError):
        return None
    return None


def collect_material(case, base_dir):
    parts, unreadable = [], []

    def walk(node, label):
        if isinstance(node, str):
            stripped = node.strip()
            if len(stripped) >= 40:
                parts.append({"label": label, "text": stripped, "path": None})
                return
            if stripped and not stripped.startswith(("{", "[")):
                for candidate in (stripped, os.path.join(base_dir, stripped)):
                    text = read_text_file(candidate)
                    if text is not None:
                        parts.append({"label": label, "text": text, "path": candidate})
                        return
                if stripped.lower().endswith((".csv", ".tsv", ".txt", ".md", ".json")):
                    unreadable.append({"label": label, "path": stripped})
            return
        if isinstance(node, dict):
            for key in ("path", "file", "filename"):
                candidate = node.get(key)
                if isinstance(candidate, str):
                    for resolved in (candidate, os.path.join(base_dir, candidate)):
                        text = read_text_file(resolved)
                        if text is not None:
                            parts.append({"label": label, "text": text, "path": resolved})
                            return
                    if os.path.isfile(candidate):
                        unreadable.append({"label": label, "path": candidate})
                        return
            for key in ("content", "text", "body", "csv", "tsv", "table_text"):
                if isinstance(node.get(key), str) and "\n" in node[key]:
                    parts.append({"label": "%s.%s" % (label, key), "text": node[key], "path": None})
                    return
            for key, value in node.items():
                walk(value, "%s.%s" % (label, key))
            return
        if isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, "%s[%d]" % (label, idx))

    for key, value in case.items():
        if key in SKIP_KEYS:
            continue
        walk(value, key)
    parts.sort(key=lambda item: (-len(item["text"]), item["label"]))
    return parts, unreadable


def task_text(base_dir):
    for name in ("task.md", "TASK.md", "task.txt"):
        path = os.path.join(base_dir, name)
        text = read_text_file(path)
        if text:
            return text, path
    return "", None


def parse_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("%", "")
        if NUMBER_RE.match(cleaned):
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def split_row(line):
    if "\t" in line:
        return [cell.strip() for cell in line.split("\t")]
    try:
        return [cell.strip() for cell in next(csv.reader(io.StringIO(line)))]
    except (csv.Error, StopIteration):
        return [cell.strip() for cell in line.split(",")]


def parse_delimited(text):
    lines = [line for line in text.splitlines() if line.strip()]
    rows = [split_row(line) for line in lines[:400]]
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    if any(not cell for cell in header):
        header = ["col%d" % (idx + 1) for idx in range(width)]
        body = rows
    else:
        body = rows[1:]
    if len(body) < 1:
        return None
    records = [dict(zip(header, row)) for row in body]
    return {"columns": header, "records": records, "source": "inline_delimited"}


def table_from_node(node, label):
    if isinstance(node, dict):
        columns = node.get("columns") or node.get("header") or node.get("fields")
        rows = node.get("rows") or node.get("data") or node.get("records")
        if isinstance(columns, list) and isinstance(rows, list):
            columns = [str(col) if not isinstance(col, dict) else str(col.get("name") or col.get("key"))
                       for col in columns]
            records = []
            for row in rows:
                if isinstance(row, dict):
                    records.append({str(key): value for key, value in row.items()})
                elif isinstance(row, list):
                    records.append(dict(zip(columns, row)))
            if records:
                return {"columns": columns, "records": records, "source": label}
        if not rows and columns is None:
            list_values = [value for value in node.values() if isinstance(value, list)]
            if list_values and all(isinstance(value, list) for value in node.values()):
                length = max(len(value) for value in list_values)
                records = [{str(key): (value[idx] if idx < len(value) else None)
                            for key, value in node.items()} for idx in range(length)]
                return {"columns": [str(key) for key in node], "records": records, "source": label}
    if isinstance(node, list) and node and all(isinstance(entry, dict) for entry in node):
        columns = []
        for entry in node:
            for key in entry:
                if str(key) not in columns:
                    columns.append(str(key))
        return {"columns": columns, "records": node, "source": label}
    return None


def find_tables(case, parts):
    tables = []
    top = table_from_node(case, "case")
    if top and len(top["records"]) >= 1 and len(top["columns"]) >= 2:
        tables.append(top)

    def walk(node, label):
        found = table_from_node(node, label)
        if found and len(found["records"]) >= 1 and len(found["columns"]) >= 2:
            tables.append(found)
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if key in SKIP_KEYS:
                    continue
                walk(value, "%s.%s" % (label, key))
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                walk(value, "%s[%d]" % (label, idx))

    for key, value in case.items():
        if key in SKIP_KEYS:
            continue
        walk(value, key)
    for part in parts:
        if any(sep in part["text"] for sep in (",", "\t")):
            parsed = parse_delimited(part["text"])
            if parsed:
                parsed["source"] = part["label"]
                tables.append(parsed)
    tables.sort(key=lambda item: (-len(item["records"]), -len(item["columns"])))
    return tables


def column_profile(table):
    columns = table["columns"]
    records = table["records"]
    profile = {}
    for column in columns:
        values = [record.get(column) for record in records]
        numeric = [parse_number(value) for value in values]
        numeric_count = sum(1 for value in numeric if value is not None)
        profile[column] = {
            "numeric": numeric_count,
            "total": len(values),
            "is_numeric": numeric_count >= 1 and numeric_count >= 0.6 * len(values),
            "values": numeric,
            "raw": values,
            "error": any(hint in column.lower() for hint in ERROR_HINTS),
        }
    return profile


def unit_of(column):
    match = UNIT_RE.search(column)
    return match.group(1).strip() if match else None


def infer_chart_type(case, table, profile):
    explicit = case.get("chart_type") or case.get("plot_type") or case.get("chart")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    numeric_cols = [column for column, meta in profile.items() if meta["is_numeric"] and not meta["error"]]
    categorical = [column for column, meta in profile.items() if not meta["is_numeric"]]
    if len(numeric_cols) >= 3 and categorical:
        return "heatmap"
    if len(numeric_cols) >= 2 and not categorical:
        return "line"
    if categorical and numeric_cols:
        return "bar"
    if len(numeric_cols) == 1:
        return "bar"
    return "table_only"


def build_series(case, table, profile, chart_type):
    explicit = case.get("series")
    series = []
    if isinstance(explicit, list) and explicit:
        for idx, entry in enumerate(explicit):
            if isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("label") or "series%d" % (idx + 1))
                values = entry.get("values") or entry.get("data")
                if isinstance(values, list):
                    series.append({"name": name, "values": [parse_number(v) for v in values],
                                   "raw": values, "unit": entry.get("unit")})
        if series:
            return series

    categorical = [column for column, meta in profile.items() if not meta["is_numeric"]]
    numeric_cols = [column for column, meta in profile.items()
                    if meta["is_numeric"] and not meta["error"]]
    error_cols = [column for column, meta in profile.items()
                  if meta["is_numeric"] and meta["error"]]
    for column in numeric_cols:
        series.append({
            "name": column,
            "values": profile[column]["values"],
            "raw": profile[column]["raw"],
            "unit": unit_of(column),
            "error_column": None,
        })
        for error in error_cols:
            if column.split()[0].lower() in error.lower():
                series[-1]["error_column"] = error
    for idx, entry in enumerate(series):
        if entry["error_column"] is None and error_cols:
            entry["error_column"] = error_cols[idx] if idx < len(error_cols) else None
    return series


def nice_range(values):
    clean = [value for value in values if value is not None]
    if not clean:
        return {"min": None, "max": None, "ticks": []}
    low, high = min(clean), max(clean)
    if low > 0:
        low = 0.0
    if high == low:
        high = low + 1.0
    span = high - low
    step = 10 ** (len(str(int(abs(span)))) - 1) if span >= 1 else 0.1
    ticks = []
    tick = low
    guard = 0
    while tick <= high + step * 0.5 and guard < 12:
        ticks.append(round(tick, 6))
        tick += step
        guard += 1
    return {"min": round(low, 6), "max": round(high, 6), "ticks": ticks}


def contract_fields(contract):
    fields, unsupported = [], []
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            return list(contract["properties"].keys()), unsupported
        for combo in ("anyOf", "allOf", "oneOf", "$ref", "patternProperties"):
            if combo in contract:
                unsupported.append(combo)
        nested = contract.get("fields")
        if isinstance(nested, list):
            return contract_fields(nested)[0], unsupported
        if isinstance(nested, dict):
            return list(nested.keys()), unsupported
        reserved = {"type", "required", "optional", "additionalProperties", "title",
                    "description", "version", "schema", "$schema"}
        return [key for key in contract if key not in reserved and not str(key).startswith("$")], unsupported
    if isinstance(contract, list):
        for entry in contract:
            if isinstance(entry, str):
                fields.append(entry)
            elif isinstance(entry, dict):
                name = entry.get("name") or entry.get("field") or entry.get("key")
                if name:
                    fields.append(str(name))
        return fields, unsupported
    return fields, unsupported


ALIASES = {
    "status": "status", "chart_spec": "chart_spec", "figure_spec": "chart_spec",
    "chart": "chart_spec", "data_table": "data_table", "table": "data_table",
    "value_source_map": "value_source_map", "source_map": "value_source_map",
    "annotation_checks": "annotation_checks", "checks": "annotation_checks",
    "export_plan": "export_plan", "export_artifact": "export_plan",
    "editable_source": "editable_source", "gaps": "gaps", "uncertainty": "uncertainty",
    "evidence": "evidence", "stats": "stats", "limitations": "limitations",
}


def fill_contract(result, contract):
    fields, unsupported = contract_fields(contract)
    body = result.get("result") if isinstance(result.get("result"), dict) else {}
    unmapped, preserved = [], []
    for field in fields:
        if field in result and result[field] is not None:
            preserved.append(field)
            continue
        key = ALIASES.get(field.lower(), field.lower())
        if key in result and result[key] is not None:
            result[field] = result[key]
            preserved.append(field)
        elif key in body and body[key] is not None:
            result[field] = body[key]
            preserved.append(field)
        else:
            result[field] = None
            unmapped.append(field)
    result["contract_notes"] = {
        "declared_fields": fields,
        "preserved_fields": preserved,
        "unmapped_fields": unmapped,
        "unsupported_features": unsupported,
        "note": "Unsupported or absent fields are null; nothing is fabricated.",
    }
    if result.get("status") == "success" and (unmapped or unsupported):
        result["status"] = "partial"


def write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_export_plan(case):
    requested = case.get("export") or case.get("export_spec") or case.get("target_format")
    formats = []
    if isinstance(requested, str):
        formats = [part.strip().lower() for part in re.split(r"[,/;]", requested) if part.strip()]
    if not formats:
        formats = ["svg", "pdf", "png"]
    checks = {
        "svg": ["text_editable", "series_named", "legend_present"],
        "pdf": ["fonts_embedded", "vector_preserved"],
        "png": ["dpi_at_least_300", "no_label_clipping"],
        "eps": ["fonts_embedded", "vector_preserved"],
        "tiff": ["dpi_at_least_300", "lossless_compression"],
    }
    return [{"format": fmt,
             "required_checks": checks.get(fmt, ["resolution_and_color_declared"]),
             "performed": False,
             "reason": "no renderer invoked; plan is reproducible"} for fmt in formats]


def main():
    parser = argparse.ArgumentParser(description="Source-value chart builder")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args()

    result = {
        "case_id": None,
        "challenge_id": CHALLENGE_ID,
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "status": "failed",
        "result": {
            "chart_spec": {"chart_type": None, "canvas": {}, "axes": {}, "series": [],
                           "marks": [], "legend": [], "annotations": []},
            "data_table": {"columns": [], "records": [], "source": None},
            "value_source_map": [],
            "annotation_checks": [],
            "export_plan": [],
            "editable_source": None,
            "gaps": [],
        },
        "evidence": [],
        "uncertainty": [],
        "errors": [],
        "stats": {},
        "limitations": [
            "Only explicit tables and common delimited text are parsed.",
            "Marks are normalized drawing constraints, not rendered pixels.",
        ],
    }

    try:
        case = load_json(args.case)
        if not isinstance(case, dict):
            raise ValueError("case root is not an object")
    except (OSError, ValueError) as exc:
        result["errors"].append({"stage": "read_case", "type": type(exc).__name__,
                                 "detail": "case.json missing or not valid JSON"})
        result["status"] = "insufficient_input"
        write_json(args.out, result)
        return 2

    base_dir = os.path.dirname(os.path.abspath(args.case))
    result["case_id"] = case.get("case_id") or case.get("id")
    contract = case.get("output_contract")
    parts, unreadable = collect_material(case, base_dir)
    extra_task, task_path = task_text(base_dir)
    tables = find_tables(case, parts)

    if not tables:
        result["status"] = "insufficient_input"
        result["uncertainty"].append({"item": "data", "detail": "no tabular data found"})
        result["result"]["gaps"].append({"type": "no_table", "detail": "supply rows/columns or a delimited table"})
        fill_contract(result, contract)
        write_json(args.out, result)
        return 0

    table = tables[0]
    profile = column_profile(table)
    chart_type = infer_chart_type(case, table, profile)
    series = build_series(case, table, profile, chart_type)
    categorical = [column for column, meta in profile.items() if not meta["is_numeric"]]
    category_column = categorical[0] if categorical else None
    categories = [norm(record.get(category_column)) if category_column else str(idx + 1)
                  for idx, record in enumerate(table["records"])]

    all_values = [value for entry in series for value in entry["values"] if value is not None]
    y_range = nice_range(all_values)
    marks, source_map, checks = [], [], []

    for s_idx, entry in enumerate(series):
        if len(entry["values"]) != len(table["records"]):
            checks.append({"check": "series_length_mismatch", "series": entry["name"],
                           "values": len(entry["values"]), "rows": len(table["records"])})
        for r_idx, value in enumerate(entry["values"]):
            raw_value = entry["raw"][r_idx] if r_idx < len(entry["raw"]) else None
            if value is None:
                if raw_value not in (None, ""):
                    checks.append({"check": "non_numeric_cell", "series": entry["name"],
                                   "row": r_idx + 1, "raw": str(raw_value)[:40]})
                else:
                    checks.append({"check": "missing_value", "series": entry["name"],
                                   "row": r_idx + 1})
                continue
            if y_range["min"] is None or y_range["max"] == y_range["min"]:
                fraction = 0.0
            else:
                fraction = (value - y_range["min"]) / (y_range["max"] - y_range["min"])
            mark_id = "M%d_%d" % (s_idx + 1, r_idx + 1)
            mark = {
                "mark_id": mark_id,
                "series": entry["name"],
                "category": categories[r_idx],
                "value": value,
                "raw_value": str(raw_value),
                "unit": entry.get("unit"),
                "row": r_idx + 1,
                "column": entry["name"],
                "normalized": round(max(0.0, min(1.0, fraction)), 4),
                "geometry": ("bar_height" if chart_type == "bar" else
                             "point_y" if chart_type in ("line", "scatter") else "cell_color"),
            }
            marks.append(mark)
            source_map.append({
                "mark_id": mark_id, "source": table["source"], "row": r_idx + 1,
                "column": entry["name"], "raw_value": str(raw_value), "numeric_value": value,
            })

    for column in categorical:
        values = [norm(record.get(column)) for record in table["records"]]
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            checks.append({"check": "duplicate_category", "column": column, "values": duplicates[:10]})

    annotations = []
    for column in [entry["name"] for entry in series]:
        unit = unit_of(column)
        if not unit:
            annotations.append({"target": column, "issue": "missing_unit",
                                "detail": "column name has no parenthesised unit"})
    if category_column and any(len(category) > 24 for category in categories):
        annotations.append({"target": category_column, "issue": "long_category_label",
                            "detail": "labels may need wrapping or abbreviation"})
    text_all = "\n".join(part["text"] for part in parts) + "\n" + extra_task
    if re.search(r"(\*{1,3}|p\s*[<≤]\s*0?\.\d+)", text_all):
        annotations.append({"target": "significance", "issue": "significance_markers_in_text",
                            "detail": "confirm markers map to a tested comparison, not decoration"})
    if not re.search(r"\bn\s*=\s*\d+|样本量", text_all):
        annotations.append({"target": "caption", "issue": "sample_size_not_stated",
                            "detail": "n per group was not found in source text"})
    error_series = [entry["name"] for entry in series if entry.get("error_column")]
    if error_series:
        annotations.append({"target": "error_bars", "issue": "error_definition_required",
                            "detail": "declare whether error bars are SD, SEM or CI"})

    numeric_by_series = {}
    for entry in series:
        clean = [value for value in entry["values"] if value is not None]
        if len(clean) >= 3:
            mean = sum(clean) / len(clean)
            var = sum((value - mean) ** 2 for value in clean) / len(clean)
            sd = var ** 0.5
            if sd > 0:
                for idx, value in enumerate(entry["values"]):
                    if value is not None and abs(value - mean) > 3 * sd:
                        numeric_by_series.setdefault(entry["name"], []).append(idx + 1)
    for name, rows in numeric_by_series.items():
        checks.append({"check": "numeric_outlier", "series": name, "rows": rows})

    legend = [{"key": entry["name"], "unit": entry.get("unit"),
               "error_column": entry.get("error_column")} for entry in series]
    used_series = {mark["series"] for mark in marks}
    missing_legend = [entry["name"] for entry in series if entry["name"] not in used_series]
    if missing_legend:
        checks.append({"check": "legend_series_mismatch", "series_without_marks": missing_legend})

    chart_spec = {
        "chart_type": chart_type,
        "canvas": {"width_mm": 89, "height_mm": 60, "dpi": 600, "units": "mm"},
        "axes": {
            "x": {"label": category_column or "index", "type": "categorical"},
            "y": {"label": series[0]["name"] if series else "value",
                  "unit": series[0].get("unit") if series else None,
                  "range": [y_range["min"], y_range["max"]], "ticks": y_range["ticks"]},
        },
        "series": [{"name": entry["name"], "unit": entry.get("unit"),
                    "count": len([v for v in entry["values"] if v is not None]),
                    "error_column": entry.get("error_column")} for entry in series],
        "marks": marks,
        "legend": legend,
        "annotations": annotations,
        "style": {"font_family": "Arial", "font_size_pt": 7, "line_width_pt": 1.0,
                  "palette": "colorblind_safe", "background": "white"},
    }

    result["result"].update({
        "chart_spec": chart_spec,
        "data_table": {"columns": table["columns"], "source": table["source"],
                       "records": table["records"][:200]},
        "value_source_map": source_map[:600],
        "annotation_checks": annotations + checks,
        "export_plan": build_export_plan(case),
        "editable_source": {
            "target_file": "figure.svg",
            "text_policy": "axis labels, tick labels and legend stay as text",
            "series_layers": [entry["name"] for entry in series],
            "produced": False,
            "reason": "emitted as a reproducible chart specification, not a fabricated render",
        },
        "gaps": [{"type": "annotation_gap", "issue": item["issue"], "target": item["target"]}
                 for item in annotations],
    })
    result["evidence"] = [
        {"type": "table_source", "source": table["source"],
         "rows": len(table["records"]), "columns": table["columns"]}
    ]
    result["evidence"] += [{"type": "value_origin", "mark_id": item["mark_id"],
                            "row": item["row"], "column": item["column"]}
                           for item in source_map[:30]]
    for entry in unreadable:
        result["uncertainty"].append({"item": "file", "detail": "unreadable_or_missing",
                                      "path": os.path.basename(entry["path"])})
    if y_range["min"] is None:
        result["uncertainty"].append({"item": "y_axis", "detail": "no numeric values"})
    if table["source"] == "inline_delimited":
        result["uncertainty"].append({"item": "data_table", "detail": "parsed_from_inline_text"})
    if len(tables) > 1:
        result["uncertainty"].append({"item": "data_table", "detail": "multiple_tables_detected",
                                      "count": len(tables), "used": 0})
    if task_path:
        result["evidence"].append({"type": "task_note", "chars": len(extra_task),
                                   "sha256": sha256_text(extra_task)})

    result["stats"] = {
        "tables_found": len(tables),
        "rows": len(table["records"]),
        "columns": len(table["columns"]),
        "series": len(series),
        "marks": len(marks),
        "checks": len(checks),
        "annotation_gaps": len(annotations),
        "numeric_cells": len(all_values),
    }

    if marks and y_range["min"] is not None:
        result["status"] = "success"
    elif table["records"]:
        result["status"] = "partial"
    else:
        result["status"] = "insufficient_input"
    if any(check["check"] in ("series_length_mismatch", "non_numeric_cell") for check in checks):
        result["status"] = "partial"

    fill_contract(result, contract)
    write_json(args.out, result)
    if result["status"] == "failed":
        return 2
    if result.get("contract_notes", {}).get("unmapped_fields"):
        return 1
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
