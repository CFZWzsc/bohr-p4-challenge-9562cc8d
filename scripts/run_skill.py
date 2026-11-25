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
VIEW_W, VIEW_H = 640, 420
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "dataset_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}
MISSING = {"", "na", "n/a", "nan", "none", "null", "nil", "-", "--", "-999", "-9999"}
DATE_RE = re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}")
UNIT_RE = re.compile(r"[\(\[]([^\)\]]{1,24})[\)\]]")
ID_RE = re.compile(r"(?:^|_)(?:id|code|no|index)(?:_|$)")
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
KEYWORD_ROUTES = (
    (("rec", "chart", "figure"), "recommendation"),
    (("aggregat", "summar", "values"), "aggregates"),
    (("caption", "legend"), "caption"),
    (("encod", "mapping"), "encoding"),
    (("export", "artifact", "file"), "exports"),
    (("check", "warn"), "checks"),
)


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
            errors.append("material root not found: %s" % (root.name or "."))
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
                errors.append("walk failed on %s: %s" % (root.name, type(exc).__name__))
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


def is_id_like(name):
    return bool(ID_RE.search(str(name).strip().lower()))


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
    raw = span / float(max(1, count))
    mag = 10 ** math.floor(math.log10(raw))
    step = mag
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if span / step <= count:
            break
    start = math.floor(lo / step) * step
    ticks, i = [], 0
    while start + i * step < hi - 1e-9:
        ticks.append(round(start + i * step, 10) + 0.0)
        i += 1
    ticks.append(round(start + i * step, 10) + 0.0)
    return ticks


def fmt1(v):
    s = "%.1f" % v
    return "0.0" if s == "-0.0" else s


def r6(v):
    return round(v, 6) + 0.0


def svg_bar(categories, means, sds, xlabel, ylabel, title):
    W, H = VIEW_W, VIEW_H
    ml, mr, mt, mb = 72, 24, 56, 84
    pw, ph = W - ml - mr, H - mt - mb
    lows = [m - s for m, s in zip(means, sds)] + [0.0]
    highs = [m + s for m, s in zip(means, sds)] + [0.0]
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        hi = lo + 1.0
    ticks = nice_ticks(lo, hi)
    ymin, ymax = ticks[0], ticks[-1]
    if ymax <= ymin:
        ymax = ymin + 1.0

    def sy(v):
        return mt + ph - (v - ymin) / (ymax - ymin) * ph

    def clampi(v):
        return max(mt, min(mt + ph, v))

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img">' % (W, H, W, H),
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (W, H),
           '<text x="%d" y="26" font-family="Arial, Helvetica, sans-serif" font-size="15" fill="#1a1a1a">%s</text>' % (ml, escape(title))]
    for t in ticks:
        y = sy(t)
        out.append('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="#e6e6e6" stroke-width="1"/>' % (ml, fmt1(y), ml + pw, fmt1(y)))
        out.append('<text x="%d" y="%s" text-anchor="end" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (ml - 8, fmt1(y + 4), t))
    n = max(1, len(categories))
    slot = pw / float(n)
    bw = min(56.0, slot * 0.6)
    zero = sy(0.0)
    for i, cat in enumerate(categories):
        x = ml + slot * i + (slot - bw) / 2.0
        top = clampi(sy(means[i]))
        y = min(top, zero)
        h = min(abs(zero - top), mt + ph - y)
        out.append('<rect x="%s" y="%s" width="%s" height="%s" fill="%s"/>' % (fmt1(x), fmt1(y), fmt1(bw), fmt1(max(0.0, h)), PALETTE[i % 5]))
        if sds[i] and sds[i] > 0:
            e0 = clampi(sy(means[i] - sds[i]))
            e1 = clampi(sy(means[i] + sds[i]))
            xc = x + bw / 2.0
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333" stroke-width="1.2"/>' % (fmt1(xc), fmt1(e1), fmt1(xc), fmt1(e0)))
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333" stroke-width="1.2"/>' % (fmt1(xc - 5), fmt1(e1), fmt1(xc + 5), fmt1(e1)))
            out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#333" stroke-width="1.2"/>' % (fmt1(xc - 5), fmt1(e0), fmt1(xc + 5), fmt1(e0)))
        label = cat if len(cat) <= 12 else cat[:11] + "…"
        out.append('<text x="%s" y="%d" text-anchor="middle" font-family="Arial" font-size="11" fill="#1a1a1a">%s</text>' % (fmt1(x + bw / 2.0), mt + ph + 18, escape(label)))
    out.append('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="#333" stroke-width="1"/>' % (ml, fmt1(zero), ml + pw, fmt1(zero)))
    out.append('<text x="%d" y="%d" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (ml + pw / 2, H - 26, escape(xlabel)))
    out.append('<text transform="translate(18,%d) rotate(-90)" text-anchor="middle" font-family="Arial" font-size="12" fill="#1a1a1a">%s</text>' % (mt + ph / 2, escape(ylabel)))
    out.append('</svg>')
    return "\n".join(out)


def svg_points(xs, ys, xlabel, ylabel, title, connect):
    W, H = VIEW_W, VIEW_H
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
           '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (W, H),
           '<text x="%d" y="26" font-family="Arial" font-size="15" fill="#1a1a1a">%s</text>' % (ml, escape(title))]
    for t in yt:
        out.append('<line x1="%d" y1="%s" x2="%d" y2="%s" stroke="#e6e6e6"/>' % (ml, fmt1(sy(t)), ml + pw, fmt1(sy(t))))
        out.append('<text x="%d" y="%s" text-anchor="end" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (ml - 8, fmt1(sy(t) + 4), t))
    for t in xt:
        out.append('<line x1="%s" y1="%d" x2="%s" y2="%d" stroke="#e6e6e6"/>' % (fmt1(sx(t)), mt, fmt1(sx(t)), mt + ph))
        out.append('<text x="%s" y="%d" text-anchor="middle" font-family="Arial" font-size="11" fill="#4d4d4d">%g</text>' % (fmt1(sx(t)), mt + ph + 18, t))
    if connect:
        pts = " ".join("%s,%s" % (fmt1(sx(x)), fmt1(sy(y))) for x, y in sorted(zip(xs, ys)))
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6"/>' % (pts, PALETTE[0]))
    for x, y in zip(xs, ys):
        out.append('<circle cx="%s" cy="%s" r="3.2" fill="%s" fill-opacity="0.75"/>' % (fmt1(sx(x)), fmt1(sy(y)), PALETTE[0]))
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


def numeric_scan(rows, idx):
    """Split one column into finite numeric values and excluded (row, token) pairs."""
    kept, excluded = [], []
    for i, r in enumerate(rows):
        tok = r[idx].strip().lower()
        if tok in MISSING:
            excluded.append((i + 1, tok))
            continue
        val = to_float(tok)
        if val is None or not math.isfinite(val):
            excluded.append((i + 1, tok))
            continue
        kept.append((i + 1, val))
    return kept, excluded


def quantile(vals, q):
    n = len(vals)
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return vals[lo] + (vals[hi] - vals[lo]) * frac


def rect_bounds(svg, width, height):
    """Parse every <rect> and report geometry plus any viewBox violations."""
    count, violations, extent = 0, [], None
    for m in re.finditer(r"<rect\b[^>]*>", svg):
        attrs = {}
        for am in re.finditer(r'([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*"([^"]*)"', m.group(0)):
            attrs[am.group(1)] = am.group(2)
        geom = []
        for key in ("x", "y", "width", "height"):
            raw = attrs.get(key)
            if raw is None:
                geom.append(0.0)
                continue
            raw = raw.strip()
            try:
                if raw.endswith("%"):
                    geom.append(float(raw[:-1]) / 100.0 * (width if key in ("x", "width") else height))
                else:
                    geom.append(float(raw))
            except ValueError:
                geom = None
                break
        if geom is None:
            violations.append({"rect": m.group(0)[:60], "reason": "non-numeric geometry"})
            continue
        x, y, w, h = geom
        count += 1
        x2, y2 = x + w, y + h
        if x < -0.01 or y < -0.01 or w < -1e-9 or h < -1e-9 or x2 > width + 0.01 or y2 > height + 0.01:
            violations.append({"rect": [r6(x), r6(y), r6(w), r6(h)]})
        if extent is None:
            extent = [x, y, x2, y2]
        else:
            extent = [min(extent[0], x), min(extent[1], y), max(extent[2], x2), max(extent[3], y2)]
    if extent is None:
        extent = [0.0, 0.0, 0.0, 0.0]
    return {"count": count, "violations": violations, "extent": [r6(v) for v in extent]}


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    for prefixes, canonical in KEYWORD_ROUTES:
        if any(key.startswith(p) for p in prefixes):
            return canonical, payload.get(canonical)
    return None, None


def pick_y_column(cols, x_col):
    for c in cols:
        if c["type"] == "numeric" and c["name"] != x_col and not is_id_like(c["name"]) and c["distinct"] >= 2:
            return c["name"]
    return None


def pick_x_column(cols, row_count, y_col):
    cats = [c for c in cols if c["type"] == "categorical" and 2 <= c["distinct"] <= 10
            and c["distinct"] < row_count and c["name"] != y_col and not is_id_like(c["name"])]
    if cats:
        return cats[0]["name"]
    dates = [c for c in cols if c["type"] == "datetime" and c["name"] != y_col
             and c["distinct"] >= 2 and not is_id_like(c["name"])]
    if dates:
        return dates[0]["name"]
    nums = [c for c in cols if c["type"] == "numeric" and c["name"] != y_col
            and not is_id_like(c["name"]) and 2 < c["distinct"] < row_count]
    if nums:
        return nums[0]["name"]
    nums = [c for c in cols if c["type"] == "numeric" and c["name"] != y_col
            and not is_id_like(c["name"]) and c["distinct"] >= 3]
    if nums:
        return nums[0]["name"]
    return None


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
        detail = getattr(exc, "strerror", None) or type(exc).__name__
        errors.append("cannot read case.json: %s" % detail)
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
                skipped.append({"path": rel, "reason": "unreadable: %s" % type(exc).__name__})
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
    excluded_count = 0
    if status != "failed":
        if not rows:
            status = "insufficient_input"
        else:
            cols = infer_types(header, rows)
            numeric = [c for c in cols if c["type"] == "numeric"]
            x_col = None
            for key in ("group_column", "x_column"):
                cand = case.get(key)
                if isinstance(cand, str) and cand in header:
                    x_col = cand
                    break
            y_col = None
            for key in ("y_column", "value_column"):
                cand = case.get(key)
                if isinstance(cand, str) and cand in header:
                    y_col = cand
                    break
            numeric_names = [c["name"] for c in numeric
                             if not is_id_like(c["name"]) and c["distinct"] >= 2]
            if (x_col is None and y_col is None and len(numeric_names) >= 2
                    and not any(c["type"] in ("categorical", "datetime") for c in cols)):
                x_col, y_col = numeric_names[0], numeric_names[1]
            if not y_col:
                y_col = pick_y_column(cols, x_col)
            if not x_col:
                x_col = pick_x_column(cols, len(rows), y_col)
            if x_col is not None and x_col == y_col:
                x_col = None

            chart = case.get("chart_type")
            xtype = next((c["type"] for c in cols if c["name"] == x_col), None)
            if chart not in ("bar", "grouped_bar", "scatter", "line", "histogram"):
                if y_col is None:
                    chart = "category_count_bar"
                elif x_col is None:
                    chart = "histogram"
                elif xtype == "datetime":
                    chart = "line"
                elif xtype == "numeric":
                    chart = "scatter"
                elif xtype == "categorical":
                    chart = "bar"
                else:
                    chart = "histogram"
            elif chart in ("bar", "grouped_bar", "scatter", "line") and (x_col is None or y_col is None):
                chart = "histogram" if y_col else "category_count_bar"

            y_idx = header.index(y_col) if y_col else None
            kept, excluded = numeric_scan(rows, y_idx) if y_idx is not None else ([], [])
            excluded_count = len(excluded)
            hygiene_note = ""
            outlier_head = None
            plot_kept = kept
            if excluded:
                checks.append({"check": "sentinel_excluded", "column": y_col,
                               "tokens": sorted({tok for _, tok in excluded}),
                               "rows": [rno for rno, _ in excluded], "status": "warn",
                               "action": "excluded before aggregation; row numbers are 1-based data rows"})
                hygiene_note += " Excluded %d missing/sentinel value(s) in %s before aggregation." % (len(excluded), y_col)
            if kept:
                vals = sorted(v for _, v in kept)
                q1, q3 = quantile(vals, 0.25), quantile(vals, 0.75)
                bounds_lo, bounds_hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
                orows = [rno for rno, v in kept if v < bounds_lo or v > bounds_hi]
                checks.append({"check": "outlier", "column": y_col, "method": "IQR*1.5", "n": len(vals),
                               "bounds": [r6(bounds_lo), r6(bounds_hi)], "rows": orows,
                               "status": "warn" if orows else "ok",
                               "action": "flagged and excluded from plotted values; raw data unchanged"})
                outlier_head = {"lo": bounds_lo, "hi": bounds_hi, "rows": orows}
                if orows:
                    plot_kept = [(rno, v) for rno, v in kept if rno not in set(orows)]
                    hygiene_note += (" %d IQR*1.5 outlier(s) (rows %s; bounds %g to %g) are excluded from the "
                                     "plotted values; raw data unchanged."
                                     % (len(orows), ", ".join(str(r) for r in orows),
                                        round(bounds_lo, 6), round(bounds_hi, 6)))
                else:
                    hygiene_note += " No IQR*1.5 outliers were detected; all valid values are included."

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
                vals = [v for _, v in plot_kept]
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
                        bins.append([r6(lo + j * width), r6(lo + (j + 1) * width)])
                    aggregates = {"mode": "histogram", "bins": bins, "counts": counts,
                                  "n": len(vals), "bin_rule": "Sturges"}
                    labels = {"x": y_col, "y": "Frequency", "units": {y_col: next((c["unit"] for c in cols if c["name"] == y_col), None)},
                              "full_labels": {}}
                    caption = "Histogram of %s (n=%d, Sturges bins=%d).%s" % (y_col, len(vals), k, hygiene_note)
                    svg = svg_hist(bins, counts, y_col, "Frequency", "Distribution of %s" % y_col)
            elif chart in ("bar", "grouped_bar"):
                xi, yi = header.index(x_col), header.index(y_col)
                val_by_row = dict(plot_kept)
                groups = {}
                for i, r in enumerate(rows):
                    v = val_by_row.get(i + 1)
                    if v is None:
                        continue
                    groups.setdefault(r[xi].strip() or "missing", []).append(v)
                if not groups:
                    status = "insufficient_input"
                else:
                    cats = sorted(groups)
                    means = [r6(mean(groups[c])) for c in cats]
                    sds = [r6(sd(groups[c])) for c in cats]
                    aggregates = {"mode": "mean_sd", "categories": cats, "means": means,
                                  "sd": sds, "n": [len(groups[c]) for c in cats]}
                    labels = {"x": x_col, "y": y_col,
                              "units": {x_col: next((c["unit"] for c in cols if c["name"] == x_col), None),
                                        y_col: next((c["unit"] for c in cols if c["name"] == y_col), None)},
                              "full_labels": {c: c for c in cats}}
                    small = [c for c in cats if len(groups[c]) < 2]
                    for c in small:
                        checks.append({"check": "group_size", "group": c, "n": len(groups[c]),
                                       "status": "warn",
                                       "action": "n<2; SD/error bar undefined for this group"})
                    if small:
                        status = "partial"
                    caption = ("Mean %s by %s; error bars show ±1 SD; n per group = %s.%s"
                               % (y_col, x_col, ", ".join("%s=%d" % (c, len(groups[c])) for c in cats), hygiene_note))
                    svg = svg_bar(cats, means, sds, x_col, y_col, "%s by %s" % (y_col, x_col))
            else:
                xi, yi = header.index(x_col), header.index(y_col)
                val_by_row = dict(plot_kept)
                pairs = []
                for i, r in enumerate(rows):
                    yv = val_by_row.get(i + 1)
                    if yv is None:
                        continue
                    xv = None
                    if chart == "scatter":
                        xtok = r[xi].strip().lower()
                        xv = None if xtok in MISSING else to_float(xtok)
                        if xv is None or not math.isfinite(xv):
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
                                      "x_range": [r6(min(xs)), r6(max(xs))],
                                      "y_range": [r6(min(ys)), r6(max(ys))],
                                      "least_squares": fit}
                        caption = "Scatter of %s versus %s (n=%d)." % (y_col, x_col, len(pairs))
                        if fit:
                            caption += " Least-squares fit: y=%.4gx+%.4g, R²=%.3f." % (
                                fit["slope"], fit["intercept"], fit["r_squared"])
                        caption += hygiene_note
                    else:
                        pairs.sort(key=lambda p: p[2])
                        xs = [i for i in range(1, len(pairs) + 1)]
                        ys = [p[1] for p in pairs]
                        aggregates = {"mode": "series", "n": len(pairs),
                                      "y_range": [r6(min(ys)), r6(max(ys))]}
                        caption = ("Line series of %s over %s (n=%d; x order given by source rows).%s"
                                   % (y_col, x_col, len(pairs), hygiene_note))
                    svg = svg_points([float(x) for x in xs], ys, x_col, y_col,
                                     "%s vs %s" % (y_col, x_col), chart == "line")

            if aggregates and outlier_head and outlier_head["rows"]:
                aggregates["outliers_excluded"] = {
                    "method": "IQR*1.5",
                    "bounds": [r6(outlier_head["lo"]), r6(outlier_head["hi"])],
                    "rows": outlier_head["rows"]}
            encoding = {"x": x_col, "y": y_col,
                        "color": "category hue (Okabe-Ito subset)" if chart in ("bar", "grouped_bar", "category_count_bar") else "single series",
                        "error_bar": "±1 SD (bar) / none (scatter, line)",
                        "mark": "bar" if chart in ("bar", "grouped_bar", "histogram", "category_count_bar") else "point" if chart == "scatter" else "line",
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
                "alternatives": ["boxplot", "dot plot"] if chart in ("bar", "grouped_bar") else ["histogram"] if chart == "scatter" else [],
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

            exports = {"svg": "figure.svg", "png": None,
                       "png_note": "no rasterizer available in stdlib-only mode; export SVG to PNG at 300 dpi externally"}
            labels["title"] = "%s vs %s" % (y_col, x_col) if y_col else (x_col or "Figure")

    if svg and status != "failed":
        bounds = rect_bounds(svg, VIEW_W, VIEW_H)
        if bounds["violations"]:
            checks.append({"check": "svg_bounds", "status": "fail", "rects": bounds["count"],
                           "viewBox": [0, 0, VIEW_W, VIEW_H], "violations": bounds["violations"]})
            if status == "success":
                status = "partial"
        else:
            checks.append({"check": "svg_bounds", "status": "ok", "rects": bounds["count"],
                           "viewBox": [0, 0, VIEW_W, VIEW_H], "extent": bounds["extent"]})
        try:
            out_dir = Path(args.out).parent
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "figure.svg").write_text(svg, encoding="utf-8")
        except OSError as exc:
            errors.append("cannot write figure.svg: %s" % type(exc).__name__)
            exports["svg"] = None
            if status == "success":
                status = "partial"
        if not checks:
            checks.append({"check": "basic", "status": "ok", "detail": "labels, units and axis titles present"})
    elif status != "failed" and not svg and status != "insufficient_input":
        status = "partial"

    result = {"recommendation": recommendation, "encoding": encoding, "aggregates": aggregates,
              "labels": labels, "caption": caption, "style": style, "exports": exports,
              "checks": checks}
    evidence = {"source": source, "column_types": {}, "rows_used": len(rows),
                "rows_excluded_sentinel": excluded_count,
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
    extras = {}
    for field in fields:
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            best = None
            if field in case:
                best = case[field]
            elif norm_key(field) in payload:
                best = payload[norm_key(field)]
            if best is None:
                best = ("no automated producer for this field; "
                        "see result, evidence and checks")
            extras[field] = {"value": best,
                             "reason": "field name does not match any alias or keyword route"}
        else:
            output[field] = value
    if extras:
        output["contract_extras"] = extras

    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % type(exc).__name__)
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
