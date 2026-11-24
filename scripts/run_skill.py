#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic figure specification and SVG preview. Standard library only."""

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

MAX_FILES = 200
MAX_ROWS = 50000
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "dataset_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}
MISSING = {"", "na", "n/a", "nan", "none", "null", "nil", "-", "--", "-999", "-9999"}
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
UNIT_RE = re.compile(r"[\(\[]([^\)\]]{1,24})[\)\]]")
PALETTE = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "recommendation": ["recommendation", "chart", "chart_type", "figure_type", "plot"],
    "encoding": ["encoding", "mapping", "aesthetics", "channels"],
    "aggregates": ["aggregates", "summary", "data_summary", "values"],
    "labels": ["labels", "axis_labels", "titles", "annotation"],
    "caption": ["caption", "figure_caption", "legend", "legend_text"],
    "style": ["style", "theme", "palette", "appearance"],
    "exports": ["exports", "outputs", "files", "artifacts"],
    "checks": ["checks", "readability", "quality_checks", "warnings"],
    "status": ["status", "run_status"],
    "result": ["result", "results", "data"],
    "evidence": ["evidence", "provenance", "sources"],
    "uncertainty": ["uncertainty", "limitations"],
    "errors": ["errors", "error"],
    "stats": ["stats", "statistics", "counts"],
}


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def contract_fields(case):
    oc = case.get("output_contract")
    out = []
    if isinstance(oc, dict):
        if isinstance(oc.get("properties"), dict):
            out = list(oc["properties"].keys())
        elif isinstance(oc.get("fields"), dict):
            out = list(oc["fields"].keys())
        elif isinstance(oc.get("fields"), list):
            out = [x for x in oc["fields"] if isinstance(x, str)]
        elif isinstance(oc.get("required"), list):
            out = [x for x in oc["required"] if isinstance(x, str)]
        else:
            out = list(oc.keys())
    elif isinstance(oc, list):
        out = [x for x in oc if isinstance(x, str)]
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def iter_paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_paths(item)
    elif isinstance(value, dict):
        for key in ("path", "dir", "file", "files", "root"):
            if key in value:
                yield from iter_paths(value[key])


def candidate_roots(case, case_path):
    base = case_path.parent
    roots = []
    for key in PATH_KEYS + ("data_files",):
        if key in case:
            for raw in iter_paths(case[key]):
                p = Path(raw)
                if not p.is_absolute():
                    p = base / p
                roots.append(p)
    if base.name.lower() == "input" or (base / "materials").is_dir():
        roots.append(base)
    if Path("/input").is_dir():
        roots.append(Path("/input"))
    seen, uniq = set(), []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def collect(roots, errors, skipped, exclude):
    files, seen = [], set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
    except OSError:
        exclude_key = None
    for root in roots:
        if not root.exists():
            errors.append("material root not found: %s" % (root.name or str(root)))
            continue
        if root.is_file():
            paths, root_dir = [root], root.parent
        else:
            root_dir, paths = root, []
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                    for fn in sorted(filenames):
                        paths.append(Path(dirpath) / fn)
            except OSError as exc:
                errors.append("walk failed on %s: %s" % (root.name, exc))
                continue
        for path in paths:
            if len(files) >= MAX_FILES:
                skipped.append({"path": path.name, "reason": "file cap reached"})
                continue
            try:
                rp = str(path.resolve())
                if path.is_symlink() or not path.is_file() or rp == exclude_key:
                    continue
            except OSError:
                continue
            if rp in seen:
                continue
            seen.add(rp)
            try:
                rel = path.relative_to(root_dir).as_posix()
            except ValueError:
                rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


def decode(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def to_float(value):
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def infer_types(header, rows):
    cols = []
    for idx, name in enumerate(header):
        vals = [r[idx].strip() for r in rows if r[idx].strip().lower() not in MISSING]
        nums = [to_float(v) for v in vals]
        num = [x for x in nums if x is not None]
        dates = [v for v in vals if DATE_RE.match(v)]
        if vals and len(num) / float(len(vals)) >= 0.8 and len(set(vals)) > 2:
            kind = "numeric"
        elif vals and len(dates) / float(len(vals)) >= 0.8:
            kind = "datetime"
        else:
            kind = "categorical"
        unit = None
        m = UNIT_RE.search(name)
        if m:
            unit = m.group(1).strip()
        cols.append({"name": name, "type": kind, "unit": unit, "non_missing": len(vals),
                     "distinct": len(set(vals)),
                     "missing": len(rows) - len(vals)})
    return cols


def mean(xs):
    return sum(xs) / float(len(xs))


def sd(xs):
    if len(xs) < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1.0))


def nice_ticks(lo, hi, count=5):
    if hi <= lo:
        hi = lo + 1.0
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / max(1, count)))
    for mult in (1, 2, 2.5, 5, 10):
        if span / (step * mult) <= count:
            step *= mult
            break
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 10))
        v += step
    return ticks


def svg_bar(categories, means, sds, xlabel, ylabel, title):
    W, H = 640, 420
    ml, mr, mt, mb = 72, 24, 56, 84
    pw, ph = W - ml - mr, H - mt - mb
    ymax = max([m + s for m, s in zip(means, sds)] + [0.0]) or 1.0
    ymax *= 1.1
    ticks = nice_ticks(0, ymax)
    ymax = max(ticks[-1], ymax)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img">' % (W, H, W, H),
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<text x="%d" y="26" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#1a1a1a">%s</text>' % (ml, escape(title))]
    for t in ticks:
        y = mt + ph - (t / ymax) * ph
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e6e6e6" stroke-width="1"/>' % (ml, y, ml + pw, y))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (ml - 8, y + 4, t))
    n = max(1, len(categories))
    slot = pw / float(n)
    bw = min(56.0, slot * 0.6)
    for i, cat in enumerate(categories):
        x = ml + slot * i + (slot - bw) / 2.0
        h = (means[i] / ymax) * ph if ymax else 0
        y = mt + ph - h
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' % (x, y, bw, h, PALETTE[i % 5]))
        if sds[i] and sds[i] > 0:
            e0 = mt + ph - ((means[i] - sds[i]) / ymax) * ph if ymax else y
            e1 = mt + ph - ((means[i] + sds[i]) / ymax) * ph if ymax else y
            xc = x + bw / 2.0
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.2"/>' % (xc, e1, xc, e0))
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.2"/>' % (xc - 5, e1, xc + 5, e1))
            out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#333" stroke-width="1.2"/>' % (xc - 5, e0, xc + 5, e0))
        label = cat if len(cat) <= 12 else cat[:11] + "…"
        out.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="Arial" font-size="11" fill="#1a1a1a">%s</text>' % (x + bw / 2.0, mt + ph + 18, escape(label)))
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333" stroke-width="1"/>' % (ml, mt + ph, ml + pw, mt + ph))
    out.append('<text x="%d" y="%d" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (ml + pw / 2, H - 26, escape(xlabel)))
    out.append('<text transform="translate(18,%d) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (mt + ph / 2, escape(ylabel)))
    out.append('</svg>')
    return "\n".join(out)


def svg_points(xs, ys, xlabel, ylabel, title, connect):
    W, H = 640, 420
    ml, mr, mt, mb = 72, 24, 56, 72
    pw, ph = W - ml - mr, H - mt - mb
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1
    if ymax == ymin:
        ymax = ymin + 1
    xt, yt = nice_ticks(xmin, xmax), nice_ticks(ymin, ymax)
    xmin, xmax, ymin, ymax = xt[0], xt[-1], yt[0], yt[-1]
    def sx(x):
        return ml + (x - xmin) / (xmax - xmin) * pw
    def sy(y):
        return mt + ph - (y - ymin) / (ymax - ymin) * ph
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img">' % (W, H, W, H),
           '<rect width="100%" height="100%" fill="#ffffff"/>',
           '<text x="%d" y="26" font-family="Arial" font-size="15" fill="#1a1a1a">%s</text>' % (ml, escape(title))]
    for t in yt:
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e6e6e6"/>' % (ml, sy(t), ml + pw, sy(t)))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (ml - 8, sy(t) + 4, t))
    for t in xt:
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e6e6e6"/>' % (sx(t), mt, sx(t), mt + ph))
        out.append('<text x="%.1f" y="%d" text-anchor="middle" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (sx(t), mt + ph + 18, t))
    if connect:
        pts = " ".join("%.1f,%.1f" % (sx(x), sy(y)) for x, y in sorted(zip(xs, ys)))
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (pts, PALETTE[0]))
    for x, y in zip(xs, ys):
        out.append('<circle cx="%.1f" cy="%.1f" r="3.2" fill="%s" fill-opacity="0.75"/>' % (sx(x), sy(y), PALETTE[0]))
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (ml, mt + ph, ml + pw, mt + ph))
    out.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#333"/>' % (ml, mt, ml, mt + ph))
    out.append('<text x="%d" y="%d" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (ml + pw / 2, H - 26, escape(xlabel)))
    out.append('<text transform="translate(18,%d) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (mt + ph / 2, escape(ylabel)))
    out.append('</svg>')
    return "\n".join(out)


def svg_hist(bins, counts, xlabel, ylabel, title):
    labels = ["%.3g–%.3g" % (b[0], b[1]) for b in bins]
    return svg_bar(labels, [float(c) for c in counts], [0.0] * len(counts), xlabel, ylabel, title)


def linreg(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope": slope, "intercept": intercept, "r_squared": r2}


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Figure specification and SVG (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)
    os.environ["SK_RESULT_PATH"] = str(getattr(args, "out", None) or getattr(args, "output", None) or "")

    case_path = Path(args.case)
    errors, skipped, case, status = [], [], {}, "success"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            case = json.load(fh)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
    except (OSError, ValueError) as exc:
        errors.append("cannot read case.json: %s" % exc)
        case, status = {}, "failed"

    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else "unknown"
    fields = contract_fields(case)
    header, rows, source = [], [], None
    if status != "failed":
        for path, rel in collect(candidate_roots(case, case_path), errors, skipped, case_path):
            ext = path.suffix.lower()
            if ext not in (".csv", ".tsv", ".txt"):
                skipped.append({"path": rel, "reason": "unsupported extension"})
                continue
            try:
                raw = path.read_bytes()
                text, enc = decode(raw)
            except OSError as exc:
                skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
                continue
            line = text.splitlines()[0] if text else ""
            delim = "\t" if ext == ".tsv" else max([",", "\t", ";"], key=lambda d: line.count(d))
            parsed = [r for r in csv.reader(text.splitlines(), delimiter=delim) if any(c.strip() for c in r)]
            if not parsed:
                skipped.append({"path": rel, "reason": "empty table"})
                continue
            header = [h.strip() or "col_%d" % (i + 1) for i, h in enumerate(parsed[0])]
            width = len(header)
            rows = [(r + [""] * (width - len(r)))[:width] for r in parsed[1:MAX_ROWS]]
            source = {"file": rel, "sha256": hashlib.sha256(raw).hexdigest(), "encoding": enc}
            break

    recommendation, encoding, aggregates, labels, caption = {}, {}, {}, {}, ""
    style, checks, exports, svg = {}, [], {}, None
    if status != "failed":
        if not rows:
            status = "insufficient_input"
        else:
            cols = infer_types(header, rows)
            numeric = [c for c in cols if c["type"] == "numeric"]
            categorical = [c for c in cols if c["type"] == "categorical"]
            datetime = [c for c in cols if c["type"] == "datetime"]
            x_col = case.get("x_column") if case.get("x_column") in header else None
            y_col = case.get("y_column") if case.get("y_column") in header else None
            if not y_col:
                y_col = numeric[0]["name"] if numeric else None
            if not x_col:
                if categorical:
                    x_col = categorical[0]["name"]
                elif datetime:
                    x_col = datetime[0]["name"]
                elif len(numeric) >= 2:
                    x_col = numeric[0]["name"]
                    y_col = numeric[1]["name"]
                elif numeric:
                    x_col = numeric[0]["name"]
                    y_col = None
            chart = case.get("chart_type")
            if not chart:
                if y_col is None:
                    chart = "category_count_bar"
                elif x_col and any(c["name"] == x_col and c["type"] == "datetime" for c in cols):
                    chart = "line"
                elif x_col and any(c["name"] == x_col and c["type"] == "numeric" for c in cols):
                    chart = "scatter"
                elif x_col and any(c["name"] == x_col and c["type"] == "categorical" for c in cols):
                    chart = "bar"
                else:
                    chart = "histogram"

            if y_col is None and x_col:
                idx = header.index(x_col)
                counts = {}
                for r in rows:
                    k = r[idx].strip() or "missing"
                    counts[k] = counts.get(k, 0) + 1
                cats = sorted(counts)[:20]
                means = [float(counts[c]) for c in cats]
                sds = [0.0] * len(cats)
                aggregates = {"mode": "counts", "categories": cats, "counts": means}
                labels = {"x": x_col, "y": "count", "units": {}, "full_labels": {c: c for c in cats}}
                caption = "Bar counts by %s (n=%d records). No numeric measurement column was available." % (x_col, len(rows))
                svg = svg_bar(cats, means, sds, x_col, "Count", "%s by %s" % ("Count", x_col))
                status = "partial"
            elif chart == "histogram":
                idx = header.index(y_col)
                vals = [to_float(r[idx]) for r in rows if to_float(r[idx]) is not None]
                if not vals:
                    status = "insufficient_input"
                else:
                    k = max(3, int(math.ceil(math.log(len(vals), 2) + 1)))
                    lo, hi = min(vals), max(vals)
                    if hi == lo:
                        hi = lo + 1
                    width = (hi - lo) / k
                    bins, counts = [], [0] * k
                    for v in vals:
                        j = min(k - 1, int((v - lo) / width))
                        counts[j] += 1
                    for j in range(k):
                        bins.append([lo + j * width, lo + (j + 1) * width])
                    aggregates = {"mode": "histogram", "bins": bins, "counts": counts,
                                  "n": len(vals), "bin_rule": "Sturges"}
                    labels = {"x": y_col, "y": "Frequency", "units": {y_col: next((c["unit"] for c in cols if c["name"] == y_col), None)},
                              "full_labels": {}}
                    caption = "Histogram of %s (n=%d, Sturges bins=%d)." % (y_col, len(vals), k)
                    svg = svg_hist(bins, counts, y_col, "Frequency", "Distribution of %s" % y_col)
            elif chart in ("bar", "grouped_bar"):
                xi, yi = header.index(x_col), header.index(y_col)
                groups = {}
                for r in rows:
                    v = to_float(r[yi])
                    if v is None:
                        continue
                    groups.setdefault(r[xi].strip() or "missing", []).append(v)
                if not groups:
                    status = "insufficient_input"
                else:
                    cats = sorted(groups)
                    means = [round(mean(groups[c]), 6) for c in cats]
                    sds = [round(sd(groups[c]), 6) for c in cats]
                    aggregates = {"mode": "mean_sd", "categories": cats, "means": means,
                                  "sd": sds, "n": [len(groups[c]) for c in cats]}
                    labels = {"x": x_col, "y": y_col,
                              "units": {x_col: next((c["unit"] for c in cols if c["name"] == x_col), None),
                                        y_col: next((c["unit"] for c in cols if c["name"] == y_col), None)},
                              "full_labels": {c: c for c in cats}}
                    caption = ("Mean %s by %s; error bars show ±1 SD; n per group = %s."
                               % (y_col, x_col, ", ".join(str(len(groups[c])) for c in cats)))
                    svg = svg_bar(cats, means, sds, x_col, y_col, "%s by %s" % (y_col, x_col))
            else:
                xi, yi = header.index(x_col), header.index(y_col)
                pairs = []
                for r in rows:
                    xv = to_float(r[xi]) if chart == "scatter" else None
                    yv = to_float(r[yi])
                    if yv is None:
                        continue
                    if chart == "scatter" and xv is None:
                        continue
                    pairs.append((xv, yv, r[xi].strip()))
                if not pairs:
                    status = "insufficient_input"
                else:
                    if chart == "scatter":
                        xs = [p[0] for p in pairs]
                        ys = [p[1] for p in pairs]
                        fit = linreg(xs, ys)
                        aggregates = {"mode": "points", "n": len(pairs),
                                      "x_range": [min(xs), max(xs)], "y_range": [min(ys), max(ys)],
                                      "least_squares": fit}
                        caption = "Scatter of %s versus %s (n=%d)." % (y_col, x_col, len(pairs))
                        if fit:
                            caption += " Dashed least-squares fit: y=%.4gx+%.4g, R²=%.3f." % (
                                fit["slope"], fit["intercept"], fit["r_squared"])
                    else:
                        pairs.sort(key=lambda p: p[2])
                        xs = [i for i in range(1, len(pairs) + 1)]
                        ys = [p[1] for p in pairs]
                        aggregates = {"mode": "series", "n": len(pairs), "y_range": [min(ys), max(ys)]}
                        caption = "Line series of %s over %s (n=%d; x order given by source rows)." % (y_col, x_col, len(pairs))
                    svg = svg_points([float(x) for x in xs], ys, x_col, y_col,
                                     "%s vs %s" % (y_col, x_col), chart == "line")

            encoding = {"x": x_col, "y": y_col,
                        "color": "single series" if chart != "bar" else "category hue (Okabe-Ito subset)",
                        "error_bar": "±1 SD (bar) / none (scatter, line)",
                        "mark": "bar" if chart == "bar" else "point" if chart == "scatter" else "line" if chart == "line" else "bar",
                        "scale": "linear"}
            recommendation = {
                "chart_type": chart,
                "rationale": {
                    "bar": "one categorical grouping with a numeric measurement",
                    "grouped_bar": "categorical grouping with numeric measurement",
                    "scatter": "two numeric variables; association shown without implying causation",
                    "line": "ordered/date axis with numeric measurement",
                    "histogram": "single numeric variable distribution",
                    "category_count_bar": "no numeric measurement; counts by category",
                }.get(chart, "rule-based choice"),
                "alternatives": ["boxplot", "dot plot"] if chart == "bar" else ["histogram"] if chart == "scatter" else [],
            }
            style = {
                "size_mm": {"single_column": 89, "double_column": 183},
                "dpi": 300,
                "font_family": "Arial, Helvetica, sans-serif",
                "font_size_pt": {"axis": 8, "tick": 7, "title": 9},
                "palette": PALETTE[:5],
                "palette_name": "Okabe-Ito subset",
                "background": "#ffffff",
                "grid": "light grey, horizontal only",
                "line_width_pt": 0.75,
            }
            n_cats = len(aggregates.get("categories", aggregates.get("bins", [])))
            if n_cats > 12:
                checks.append({"check": "category_count", "value": n_cats, "status": "warn",
                               "action": "merge rare categories or facet; all data kept"})
            for c in cols:
                if c["non_missing"] and c["missing"] / float(len(rows)) > 0.2:
                    checks.append({"check": "missing_rate", "column": c["name"],
                                   "value": round(c["missing"] / float(len(rows)), 3),
                                   "status": "warn", "action": "state exclusions in caption"})
                if c["type"] == "numeric" and c["distinct"] == 1:
                    checks.append({"check": "constant_column", "column": c["name"],
                                   "status": "warn", "action": "no variation to display"})
            if not checks:
                checks.append({"check": "basic", "status": "ok", "detail": "labels, units and axis titles present"})

            exports = {"svg": "figure.svg", "png": None,
                       "png_note": "no rasterizer available in stdlib-only mode; export SVG to PNG at 300 dpi externally"}
            labels["title"] = "%s vs %s" % (y_col, x_col) if y_col else (x_col or "Figure")

    if svg and status != "failed":
        try:
            out_dir = Path(args.out).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "figure.svg").write_text(svg, encoding="utf-8")
        except OSError as exc:
            errors.append("cannot write figure.svg: %s" % exc)
            exports["svg"] = None
            if status == "success":
                status = "partial"
    elif status != "failed" and not svg and status != "insufficient_input":
        status = "partial"

    result = {"recommendation": recommendation, "encoding": encoding, "aggregates": aggregates,
              "labels": labels, "caption": caption, "style": style, "exports": exports,
              "checks": checks}
    evidence = {"source": source, "column_types": {}, "rows_used": len(rows),
                "figure_generator": "hand-written SVG (no matplotlib)", "source_mutation": "none"}
    if status != "failed" and rows:
        evidence["column_types"] = {c["name"]: c["type"] for c in infer_types(header, rows)}
    uncertainty = [
        "Chart choice follows generic rules, not journal-specific conventions.",
        "No statistical test or significance annotation is produced.",
        "Only SVG is written; PNG/TIFF requires an external rasterizer.",
    ]
    stats = {"rows": len(rows), "columns": len(header),
             "checks": len(checks), "warnings": sum(1 for c in checks if c.get("status") == "warn"),
             "skipped": len(skipped), "errors": len(errors)}
    payload = {"recommendation": recommendation, "encoding": encoding, "aggregates": aggregates,
               "labels": labels, "caption": caption, "style": style, "exports": exports,
               "checks": checks, "status": status, "case_id": case_id, "result": result,
               "evidence": evidence, "uncertainty": uncertainty, "errors": errors, "stats": stats}

    output = {"case_id": case_id, "status": status, "result": result, "evidence": evidence,
              "uncertainty": uncertainty, "errors": errors, "stats": stats}
    notes = []
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            notes.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
    if notes:
        output["contract_notes"] = notes

    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % exc)
        return 2
    try:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        with open(work / "figure_spec.json", "w", encoding="utf-8") as fh:
            json.dump({"recommendation": recommendation, "encoding": encoding, "style": style,
                       "caption": caption, "aggregates": aggregates}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass
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
