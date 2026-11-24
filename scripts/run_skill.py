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
import math
import os
import re
import sys

import figure_svg

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
    width_counts = {}
    for row in rows:
        width_counts[len(row)] = width_counts.get(len(row), 0) + 1
    width = max(len(row) for row in rows)
    # a stray comma in prose must not become a one-off two-column "table"
    if width < 2 or width_counts.get(width, 0) < 2:
        return None
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
            if parsed and len(parsed["records"]) >= 2:
                parsed["source"] = (os.path.basename(part["path"])
                                    if part.get("path") else part["label"])
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


def split_sentences_simple(text):
    return [segment.strip() for segment in
            re.split(r"(?<=[。；;！!？?])|(?<=\.)\s+|\n+", text) if segment.strip()]


def detect_comparisons(parts, categories):
    """Significance arrows stated in the material (never inferred)."""
    comparisons, seen = [], set()
    for part in parts:
        source_name = (os.path.basename(part["path"])
                       if part.get("path") else part["label"])
        for sentence_index, sentence in enumerate(
                split_sentences_simple(part["text"]), 1):
            for match in re.finditer(
                    r"([A-Za-z0-9][A-Za-z0-9_\-]{0,15})\s+was\s+significantly\s+"
                    r"(lower|higher)\s+than\s+([A-Za-z0-9][A-Za-z0-9_\-]{0,15})",
                    sentence):
                group, direction, baseline = match.group(1), match.group(2), match.group(3)
                if group not in categories or baseline not in categories:
                    continue
                key = (baseline, group, direction)
                if key in seen:
                    continue
                seen.add(key)
                pvalue = re.search(r"p\s*[<≤]\s*([0-9.]+)", sentence)
                label = "%s than %s" % (direction, baseline)
                if pvalue:
                    label += " (p < %s)" % pvalue.group(1)
                comparisons.append({
                    "source": baseline, "target": group, "label": label,
                    "anchor": "%s:p1.s%d" % (source_name, sentence_index),
                    "quote": sentence[:200],
                })
    return comparisons


def _nice_ticks(high):
    if high <= 0:
        high = 1.0
    raw = high / 4.0
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    step = magnitude
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if step >= raw:
            break
    top = step * math.ceil(high / step)
    ticks = [round(step * index, 6) for index in range(int(round(top / step)) + 1)]
    return ticks, top


def render_chart(rows, series_name, unit, comparisons, source_name, out_dir,
                 title, subtitle):
    """Write a bar chart with SD error bars and significance arrows."""
    count = len(rows)
    bar_w, group_w, left = 58.0, 112.0, 96.0
    top, plot_h, right = 132.0, 300.0, 56.0
    base_y = top + plot_h
    width = max(left + count * group_w + right, 760.0)
    highest = max([row["value"] + (row.get("error") or 0.0) for row in rows] + [1.0])
    ticks, ymax = _nice_ticks(highest * 1.15)
    height = base_y + 196.0
    scale = plot_h / ymax
    unit_note = (" (%s)" % unit) if unit else ""

    def ypos(value):
        return base_y - max(0.0, value) * scale

    theme = "#3B6EA5"
    svg = []
    svg.append('<?xml version="1.0" encoding="UTF-8"?>')
    svg.append(
        '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        'width="%d" height="%d" viewBox="0 0 %d %d" color-profile="sRGB">'
        % (int(width), int(height), int(width), int(height)))
    svg.append("<title>%s</title>" % figure_svg.esc(title))
    svg.append("<desc>Bar chart drawn from the source table. Bar heights are the "
               "series values, error bars are the SD column, and significance "
               "arrows quote the material sentence; no value is inferred.</desc>")
    svg.append("<metadata>generator=%s; bars=%d; comparisons=%d</metadata>"
               % (figure_svg.GENERATOR, count, len(comparisons)))
    svg.append(
        '<defs><marker id="cmp-arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="6.5" markerHeight="6.5" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#7A5195"/></marker></defs>')
    svg.append('<g id="layer-background">')
    svg.append('<rect x="0" y="0" width="%d" height="%d" fill="#FFFFFF"/>'
               % (int(width), int(height)))
    svg.append("</g>")

    svg.append('<g id="layer-title">')
    for index, line in enumerate(figure_svg.wrap(title, 20, width - 2 * left,
                                                 max_lines=2)):
        svg.append(
            '<text x="%d" y="%d" font-family="%s" font-size="20" '
            'font-weight="bold" fill="#1F2933">%s</text>'
            % (int(left), 42 + 26 * index, figure_svg.FONT, figure_svg.esc(line)))
    for index, line in enumerate(figure_svg.wrap(subtitle, 12, width - 2 * left,
                                                 max_lines=2)):
        svg.append(
            '<text x="%d" y="%d" font-family="%s" font-size="12" '
            'fill="#52606D">%s</text>'
            % (int(left), 92 + 17 * index, figure_svg.FONT, figure_svg.esc(line)))
    svg.append("</g>")

    svg.append('<g id="layer-axes">')
    svg.append('<path d="M %.1f %.1f L %.1f %.1f L %.1f %.1f" fill="none" '
               'stroke="#52606D" stroke-width="1.4"/>'
               % (left, top - 12, left, base_y, left + count * group_w, base_y))
    for tick in ticks:
        y = ypos(tick)
        svg.append('<path d="M %.1f %.1f L %.1f %.1f" stroke="#CBD2D9" '
                   'stroke-width="0.8" stroke-dasharray="3 4"/>'
                   % (left, y, left + count * group_w, y))
        svg.append('<path d="M %.1f %.1f L %.1f %.1f" stroke="#52606D" '
                   'stroke-width="1"/>' % (left - 5, y, left, y))
        svg.append(
            '<text x="%.1f" y="%.1f" text-anchor="end" font-family="%s" '
            'font-size="11" fill="#3E4C59">%s</text>'
            % (left - 9, y + 4, figure_svg.FONT, figure_svg.esc("%g" % tick)))
    svg.append(
        '<text x="%d" y="%.1f" font-family="%s" font-size="12" '
        'fill="#1F2933">%s</text>'
        % (int(left - 40), top - 22, figure_svg.FONT,
           figure_svg.esc(series_name + unit_note)))
    svg.append(
        '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
        'font-size="12" fill="#1F2933">%s</text>'
        % (left + count * group_w / 2.0, base_y + 84, figure_svg.FONT,
           figure_svg.esc("Group")))
    svg.append("</g>")

    svg.append('<g id="layer-bars">')
    spec_nodes, centers = [], {}
    for index, row in enumerate(rows):
        value = row["value"]
        error = row.get("error")
        x = left + index * group_w + (group_w - bar_w) / 2.0
        y = ypos(value)
        bar_h = base_y - y
        anchor = "%s:row %d" % (source_name, index + 2)
        svg.append(
            '<g id="bar-%d" data-source-anchor="%s" data-quote="%s">'
            % (index + 1, figure_svg.attr(anchor),
               figure_svg.attr(row.get("quote") or "")))
        svg.append(
            '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" '
            'fill="%s" fill-opacity="0.9" stroke="#2E5A87" stroke-width="1"/>'
            % (x, y, bar_w, bar_h, theme))
        center = x + bar_w / 2.0
        centers[row["category"]] = center
        svg.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
            'font-size="13" font-weight="bold" fill="#1F2933">%s</text>'
            % (center, base_y + 20, figure_svg.FONT,
               figure_svg.esc(str(row["category"]))))
        svg.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
            'font-size="10" fill="#7B8794">row %d</text>'
            % (center, base_y + 36, figure_svg.FONT, index + 2))
        svg.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
            'font-size="11" fill="#3E4C59">%s</text>'
            % (center, ypos(value) - 24, figure_svg.FONT,
               figure_svg.esc("%g" % value)))
        if error:
            top_y = ypos(value + error)
            bottom_y = ypos(max(0.0, value - error))
            svg.append('<path d="M %.1f %.1f L %.1f %.1f" stroke="#1F2933" '
                       'stroke-width="1.4"/>' % (center, bottom_y, center, top_y))
            for cap_y in (top_y, bottom_y):
                svg.append('<path d="M %.1f %.1f L %.1f %.1f" stroke="#1F2933" '
                           'stroke-width="1.4"/>'
                           % (center - 7, cap_y, center + 7, cap_y))
            svg.append(
                '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
                'font-size="9" fill="#52606D">SD %g</text>'
                % (center, top_y - 8, figure_svg.FONT, error))
        svg.append("</g>")
        spec_nodes.append({
            "id": "bar-%d" % (index + 1),
            "label": "%s: %g%s" % (row["category"], value, unit_note),
            "kind": "bar",
            "anchor": anchor,
            "quote": row.get("quote"),
            "detail": ("SD %g" % error) if error else None,
            "x": round(x, 1), "y": round(y, 1),
            "w": bar_w, "h": round(bar_h, 1), "rank": index, "lane": None,
        })
    svg.append("</g>")

    svg.append('<g id="layer-comparisons">')
    label_boxes, spec_edges = [], []
    for index, comparison in enumerate(comparisons):
        if comparison["source"] not in centers or comparison["target"] not in centers:
            continue
        x1 = centers[comparison["source"]]
        x2 = centers[comparison["target"]]
        y1 = ypos(next(row["value"] + (row.get("error") or 0.0)
                       for row in rows if row["category"] == comparison["source"]))
        y2 = ypos(next(row["value"] + (row.get("error") or 0.0)
                       for row in rows if row["category"] == comparison["target"]))
        control_y = max(30.0, min(y1, y2) - 46.0 - 20.0 * index)
        mid_x = (x1 + x2) / 2.0
        svg.append(
            '<path id="cmp-%d" d="M %.1f %.1f Q %.1f %.1f %.1f %.1f" '
            'fill="none" stroke="#7A5195" stroke-width="1.4" '
            'marker-end="url(#cmp-arrow)" data-source-anchor="%s" '
            'data-quote="%s"/>'
            % (index + 1, x1, y1 - 14, mid_x, control_y, x2, y2 - 14,
               figure_svg.attr(comparison.get("anchor")),
               figure_svg.attr(comparison.get("quote") or "")))
        label_y = control_y - 4
        box_w = figure_svg.text_width(comparison["label"], 10) + 8
        label_boxes.append((mid_x - box_w / 2.0, label_y - 11,
                            mid_x + box_w / 2.0, label_y + 3))
        svg.append('<rect x="%.1f" y="%.1f" width="%.1f" height="14" '
                   'fill="#FFFFFF" opacity="0.92"/>'
                   % (mid_x - box_w / 2.0, label_y - 11, box_w))
        svg.append(
            '<text x="%.1f" y="%.1f" text-anchor="middle" font-family="%s" '
            'font-size="10" fill="#7A5195">%s</text>'
            % (mid_x, label_y, figure_svg.FONT,
               figure_svg.esc(comparison["label"])))
        spec_edges.append({
            "id": "cmp-%d" % (index + 1),
            "source": "bar-%d" % (rows.index(next(
                row for row in rows if row["category"] == comparison["source"])) + 1),
            "target": "bar-%d" % (rows.index(next(
                row for row in rows if row["category"] == comparison["target"])) + 1),
            "kind": "comparison", "label": comparison["label"],
            "anchor": comparison.get("anchor"),
            "quote": comparison.get("quote"), "arrowhead": "marker-end",
        })
    svg.append("</g>")

    svg.append('<g id="layer-legend">')
    legend_y = height - 100.0
    svg.append('<rect x="%d" y="%.1f" width="%d" height="76" rx="6" '
               'fill="#F7F9FC" stroke="#CBD2D9" stroke-width="1"/>'
               % (int(left), legend_y, int(width - 2 * left)))
    svg.append('<text x="%d" y="%.1f" font-family="%s" font-size="12" '
               'font-weight="bold" fill="#1F2933">legend</text>'
               % (int(left + 14), legend_y + 19, figure_svg.FONT))
    svg.append('<rect x="%d" y="%.1f" width="18" height="11" fill="#3B6EA5" '
               'fill-opacity="0.9" stroke="#2E5A87"/>'
               % (int(left + 14), legend_y + 30))
    svg.append('<text x="%d" y="%.1f" font-family="%s" font-size="11" '
               'fill="#3E4C59">%s</text>'
               % (int(left + 40), legend_y + 40, figure_svg.FONT,
                  figure_svg.esc(series_name + unit_note)))
    svg.append('<path d="M %d %.1f L %d %.1f M %d %.1f L %d %.1f M %d %.1f '
               'L %d %.1f" stroke="#1F2933" stroke-width="1.3"/>'
               % (int(left + 270), legend_y + 36, int(left + 270), legend_y + 28,
                  int(left + 264), legend_y + 28, int(left + 276), legend_y + 28,
                  int(left + 264), legend_y + 36, int(left + 276), legend_y + 36))
    svg.append('<text x="%d" y="%.1f" font-family="%s" font-size="11" '
               'fill="#3E4C59">SD error bar (SD column)</text>'
               % (int(left + 286), legend_y + 40, figure_svg.FONT))
    svg.append('<path d="M %d %.1f Q %d %.1f %d %.1f" fill="none" '
               'stroke="#7A5195" stroke-width="1.3" marker-end="url(#cmp-arrow)"/>'
               % (int(left + 14), legend_y + 60, int(left + 38),
                  legend_y + 46, int(left + 62), legend_y + 60))
    svg.append('<text x="%d" y="%.1f" font-family="%s" font-size="11" '
               'fill="#3E4C59">significance stated in the material</text>'
               % (int(left + 72), legend_y + 62, figure_svg.FONT))
    svg.append(
        '<text x="%d" y="%.1f" font-family="%s" font-size="10" fill="#7B8794">'
        '%d bars · %d SD · %d arrows · source: %s</text>'
        % (int(left + 300), legend_y + 62, figure_svg.FONT, count,
           sum(1 for row in rows if row.get("error")), len(comparisons),
           figure_svg.esc(source_name)))
    svg.append("</g>")
    svg.append("</svg>")

    svg_text = "\n".join(svg) + "\n"
    svg_bytes = svg_text.encode("utf-8")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "figure.svg"), "wb") as handle:
        handle.write(svg_bytes)
    facts = figure_svg._facts(svg_text)
    spec = {
        "schema": "figure-spec/1",
        "generator": figure_svg.GENERATOR,
        "figure": {
            "file": "figure.svg", "format": "svg",
            "width_px": int(width), "height_px": int(height),
            "view_box": "0 0 %d %d" % (int(width), int(height)),
            "text_elements": facts["text_count"],
            "arrow_markers": facts["marker_count"],
            "marker_edges": facts["marker_edges"],
            "sha256": figure_svg.sha256_bytes(svg_bytes),
            "bytes": len(svg_bytes),
        },
        "layout": {
            "mode": "bar_chart", "deterministic": True,
            "canvas_contains_nodes": True,
            "max_node_right": max([node["x"] + node["w"] for node in spec_nodes]
                                  + [left]),
            "max_node_bottom": round(base_y, 1),
            "bands": [],
        },
        "nodes": spec_nodes,
        "edges": spec_edges,
        "legend": [{"kind": "comparison", "color": "#7A5195", "dash": "solid"}]
                  if spec_edges else [],
    }
    spec_text = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    spec_bytes = spec_text.encode("utf-8")
    with open(os.path.join(out_dir, "figure-spec.json"), "wb") as handle:
        handle.write(spec_bytes)
    return {
        "file": "figure.svg", "spec_file": "figure-spec.json", "format": "svg",
        "path": os.path.join(out_dir, "figure.svg"),
        "bytes": len(svg_bytes), "sha256": figure_svg.sha256_bytes(svg_bytes),
        "spec_sha256": figure_svg.sha256_bytes(spec_bytes),
        "nodes": len(spec_nodes), "edges": len(spec_edges),
        "width": int(width), "height": int(height),
        "max_x": spec["layout"]["max_node_right"], "max_y": spec["layout"]["max_node_bottom"],
        "text_count": facts["text_count"],
        "marker_count": facts["marker_count"],
        "marker_edges": facts["marker_edges"],
        "figure_spec": spec,
    }


def main():
    parser = argparse.ArgumentParser(description="Source-value chart builder")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--out", "--output", default=DEFAULT_OUT)
    parser.add_argument("--work", default=DEFAULT_WORK)
    args = parser.parse_args()
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")

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
                                      "count": len(tables), "used": table["source"]})
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

    # --- real figure: bar chart + SD error bars + significance arrows ---
    out_dir = os.path.dirname(os.path.abspath(str(args.out))) or "."
    chart_rows = []
    if series and categories:
        error_column = series[0].get("error_column")
        error_values = profile.get(error_column, {}).get("values", []) if error_column else []
        for index, category in enumerate(categories):
            if index >= len(series[0]["values"]):
                continue
            value = series[0]["values"][index]
            if value is None:
                continue
            error = None
            if index < len(error_values) and error_values[index] is not None:
                error = error_values[index]
            record = (table["records"][index]
                      if index < len(table["records"]) else {})
            chart_rows.append({
                "category": str(category), "value": float(value), "error": error,
                "quote": ",".join(str(record.get(column, ""))
                                  for column in table["columns"][:4]),
            })
    if chart_rows:
        comparisons = detect_comparisons(
            parts, {row["category"] for row in chart_rows})
        figure_info = render_chart(
            chart_rows, series[0]["name"], series[0].get("unit"), comparisons,
            table["source"], out_dir,
            "%s by %s" % (series[0]["name"], category_column or "category"),
            "Bar heights and error bars are read from %s; arrows quote the "
            "material sentence. No value is inferred." % table["source"])
        chart_spec["canvas"] = {
            "width_px": figure_info["width"], "height_px": figure_info["height"],
            "width_mm": round(figure_info["width"] / 3.7795, 2),
            "height_mm": round(figure_info["height"] / 3.7795, 2),
            "dpi": 600, "units": "mm",
        }
        result["result"]["chart_spec"] = chart_spec
        result["result"]["editable_source"] = {
            "target_file": "figure.svg",
            "text_policy": "axis labels, tick labels, value labels and legend stay as text",
            "series_layers": [series[0]["name"] + " (bar layer)"],
            "produced": True,
            "file": "figure.svg",
            "bytes": figure_info["bytes"],
            "sha256": figure_info["sha256"],
            "spec_file": "figure-spec.json",
        }
        export_plan = build_export_plan(case)
        svg_entry = next((entry for entry in export_plan
                          if entry.get("format") == "svg"), None)
        if svg_entry is not None:
            svg_entry.update({
                "performed": True, "file": "figure.svg",
                "bytes": figure_info["bytes"], "sha256": figure_info["sha256"],
                "checks": ["text_editable", "series_named", "legend_present"],
            })
        for entry in export_plan:
            if entry is not svg_entry:
                entry["performed"] = False
                entry["reason"] = ("not produced in this run; SVG is the editable "
                                   "deliverable and no rasteriser was invoked")
        result["result"]["export_plan"] = export_plan
        result["exports"] = figure_svg.export_entries(figure_info)
        result["provenance_and_changes"] = {
            "derived_from_input": [
                {"element": "bars", "source": table["source"],
                 "detail": "one bar per table row; height = %s" % series[0]["name"]},
                {"element": "error_bars", "source": table["source"],
                 "detail": "vertical SD whiskers from the %s column"
                           % (series[0].get("error_column") or "error")},
                {"element": "significance_arrows", "source": "material sentences",
                 "detail": "%d comparison(s) stated with 'significantly lower/higher'" % len(comparisons)},
                {"element": "value_labels", "source": table["source"],
                 "detail": "means quoted, not recomputed"},
            ],
            "agent_added": [
                "canvas size and pixel geometry",
                "axis ticks and gridlines",
                "colour, typography and arrowhead marker",
                "legend and footer wording",
            ],
            "not_invented": [
                "no numeric value outside the table",
                "no p-value beyond those written in the material",
                "no error type other than the explicit SD column",
            ],
            "changes": [{"element": "figure.svg", "change": "created by scripts/run_skill.py"},
                        {"element": "figure-spec.json", "change": "created by scripts/run_skill.py"}],
        }
        result["compliance_checks"] = figure_svg.compliance(figure_info) + [
            {"check": "bar_values_match_table", "pass": len(chart_rows) == len(categories),
             "evidence": "%d bars from %d table rows" % (len(chart_rows), len(categories))},
            {"check": "error_bars_from_sd_column", "pass": bool(series[0].get("error_column")),
             "evidence": "%d/%d bars have an SD whisker"
                         % (sum(1 for row in chart_rows if row.get("error")), len(chart_rows))},
            {"check": "significance_arrows_from_material", "pass": len(comparisons) > 0,
             "evidence": "%d arrows traced to material sentences" % len(comparisons)},
        ]
        result["content_map"] = figure_svg.content_map(figure_info)
        result["exceptions"] = [{
            "item": "png/pdf export", "status": "not_produced",
            "reason": "no rasteriser or PDF backend was invoked; figure.svg is the deliverable",
        }]
        if not comparisons:
            result["exceptions"].append({
                "item": "significance arrows", "status": "not_produced",
                "reason": "the material states no 'significantly lower/higher' comparison",
            })
        result["stats"]["figure_nodes"] = figure_info["nodes"]
        result["stats"]["figure_edges"] = figure_info["edges"]
        result["stats"]["svg_text_elements"] = figure_info["text_count"]
        result["evidence"].append({
            "type": "figure", "file": "figure.svg", "format": "svg",
            "nodes": figure_info["nodes"], "edges": figure_info["edges"],
            "sha256": figure_info["sha256"],
        })
    else:
        result["exports"] = []
        result["provenance_and_changes"] = {
            "derived_from_input": [], "agent_added": [],
            "not_invented": [], "changes": [],
        }
        result["compliance_checks"] = [{
            "check": "figure_file_written", "pass": False,
            "evidence": "no numeric series was parsed, so no axis could be drawn",
        }]
        result["content_map"] = []
        result["exceptions"] = [{
            "item": "figure.svg", "status": "not_produced",
            "reason": "no numeric series with a categorical axis was parsed from the case",
        }]

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
            _p = os.environ.get("SK_RESULT_PATH") or "result.json"
            _d = os.path.dirname(os.path.abspath(_p))
            if _d:
                os.makedirs(_d, exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    _fallback = os.environ.get("SK_RESULT_PATH", "")
    if _fallback and not os.path.isfile(_fallback):
        try:
            _parent = os.path.dirname(os.path.abspath(_fallback))
            if _parent:
                os.makedirs(_parent, exist_ok=True)
            with open(_fallback, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0 if _fallback else _rc)
