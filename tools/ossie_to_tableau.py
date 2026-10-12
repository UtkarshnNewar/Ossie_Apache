"""Proof-of-concept emitter: Apache Ossie semantic model (YAML) -> Tableau workbook (.twb).

Usage:
    python tools/ossie_to_tableau.py Knowledge/sales_dashboard_ossie.yaml \
        output_models/Tableau/sales_dashboard.twb [--connector postgres] [--server localhost]

What is produced
  * datasource with a physical connection, one relation per Ossie dataset and left joins
    built from Ossie relationships
  * one Tableau field per Ossie field (role/type inferred)
  * one Tableau calculated field per Ossie metric (ANSI SQL -> Tableau calc language)
  * Tableau parameters for literal "year" metrics
  * a starter worksheet
  * <name>.loss_report.md listing what was mapped cleanly, approximated, or dropped

The converter reads the portable ANSI_SQL expression of each metric and only falls back to
reporting it as untranslated; it never silently drops a metric.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path

import yaml
from lxml import etree

TABLEAU_VERSION = "18.1"
SOURCE_BUILD = "2026.2.0 (20262.26.0912.1023)"  # required attribute; value only informational
ANSI = "ANSI_SQL"

# Ossie datatype -> (tableau datatype, remote-type code, aggregation)
TYPE_MAP = {
    "String": ("string", 129, "Count"),
    "Integer": ("integer", 20, "Sum"),
    "Decimal": ("real", 5, "Sum"),
    "Float": ("real", 5, "Sum"),
    "DateTime": ("datetime", 135, "Year"),
    "Date": ("date", 7, "Year"),
    "Boolean": ("boolean", 11, "Count"),
}


# --------------------------------------------------------------------------- helpers
def ansi_expr(node: dict) -> str | None:
    for d in (node.get("expression") or {}).get("dialects", []):
        if d.get("dialect") == ANSI:
            return d.get("expression")
    return None


def vendor_ext(node: dict, vendor: str) -> dict:
    for ext in node.get("custom_extensions") or []:
        if ext.get("vendor_name") == vendor:
            try:
                return json.loads(ext.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def titleize(name: str) -> str:
    """cy_sales -> CY Sales ; pct_diff_sales -> Pct Diff Sales"""
    small_upper = {"cy", "py", "kpi", "yoy", "id"}
    return " ".join(w.upper() if w in small_upper else w.capitalize() for w in name.split("_"))


def matching_paren(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "(":
            depth += 1
        elif s[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced parenthesis in: {s}")


def matching_paren_back(s: str, close_idx: int) -> int:
    depth = 0
    for i in range(close_idx, -1, -1):
        if s[i] == ")":
            depth += 1
        elif s[i] == "(":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced parenthesis in: {s}")


# --------------------------------------------------------------------------- SQL -> Tableau
class Translation:
    def __init__(self, formula: str, notes: list[str], ok: bool = True):
        self.formula, self.notes, self.ok = formula, notes, ok


def sql_to_tableau(sql: str, columns: dict[str, str], year_refs: dict[str, str]) -> Translation:
    """Translate the ANSI subset used by Ossie metrics into a Tableau calculation.

    columns   : "dataset.column" -> Tableau field name (without brackets)
    year_refs : literal year -> Tableau reference, e.g. "2023" -> "[Parameters].[Parameter 1]"
    """
    notes: list[str] = []
    s = " ".join(sql.split())

    # 1. window functions: AGG(...) OVER ()  ->  WINDOW_AGG(...)
    while True:
        m = re.search(r"\)\s*OVER\s*\(\s*\)", s)
        if not m:
            break
        close = m.start()
        open_ = matching_paren_back(s, close)
        fn = re.search(r"([A-Za-z_]+)\s*$", s[:open_])
        if not fn or fn.group(1).upper() not in {"AVG", "MIN", "MAX", "SUM", "COUNT"}:
            return Translation(sql, ["unsupported window function"], ok=False)
        name = fn.group(1).upper()
        s = s[: fn.start()] + f"WINDOW_{name}" + s[open_ : close + 1] + s[m.end() :]
    if "WINDOW_" in s:
        notes.append("OVER () mapped to Tableau WINDOW_* table calculation "
                     "(depends on the view's partitioning, not model-level)")

    # 2. NULLIF(a, 0) -> a   (Tableau returns NULL on division by zero)
    while True:
        m = re.search(r"NULLIF\s*\(", s)
        if not m:
            break
        open_ = s.index("(", m.start())
        close = matching_paren(s, open_)
        inner = s[open_ + 1 : close]
        depth, split = 0, None
        for i, ch in enumerate(inner):
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                split = i
        if split is None or inner[split + 1 :].strip() != "0":
            return Translation(sql, ["unsupported NULLIF form"], ok=False)
        s = s[: m.start()] + "(" + inner[:split].strip() + ")" + s[close + 1 :]
        if not any("NULLIF" in n for n in notes):
            notes.append("NULLIF(x, 0) dropped: Tableau already returns NULL on divide-by-zero")

    # 3. simple rewrites
    s = re.sub(r"EXTRACT\s*\(\s*YEAR\s+FROM\s+", "YEAR(", s, flags=re.I)
    s = re.sub(r"COUNT\s*\(\s*DISTINCT\s+", "COUNTD(", s, flags=re.I)
    s = re.sub(r"\bCASE\s+WHEN\b", "IF", s)
    if re.search(r"\bCASE\b", s) or len(re.findall(r"\bWHEN\b", s)):
        return Translation(sql, ["unsupported CASE form"], ok=False)
    s = re.sub(r"'([^']*)'", r'"\1"', s)

    # 4. column references
    def col(m: re.Match) -> str:
        key = f"{m.group(1)}.{m.group(2)}"
        return f"[{columns[key]}]" if key in columns else m.group(0)

    s = re.sub(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", col, s)

    # 5. literal years -> parameter / calc references
    def year(m: re.Match) -> str:
        lit = m.group(2)
        return f"{m.group(1)}{year_refs[lit]}" if lit in year_refs else m.group(0)

    s = re.sub(r"(YEAR\([^()]*\)\s*=\s*)(\d{4})\b", year, s)

    leftover = re.findall(r"\b(EXTRACT|NULLIF|OVER|CASE|WHEN)\b", s)
    if leftover:
        return Translation(sql, [f"untranslated tokens: {sorted(set(leftover))}"], ok=False)
    return Translation(s, notes)


# --------------------------------------------------------------------------- model reading
class Model:
    def __init__(self, doc: dict):
        sm = doc["semantic_model"]
        self.sm = sm[0] if isinstance(sm, list) else sm
        self.name = self.sm["name"]
        self.datasets = self.sm.get("datasets", [])
        self.relationships = self.sm.get("relationships", [])
        self.metrics = self.sm.get("metrics", [])

    def fact_first(self) -> list[dict]:
        """Order datasets so the fact (a 'from' side, never a 'to') is first, then BFS."""
        by_name = {d["name"]: d for d in self.datasets}
        tos = {r["to"] for r in self.relationships}
        roots = [r["from"] for r in self.relationships if r["from"] not in tos]
        order: list[str] = []
        for root in roots or [self.datasets[0]["name"]]:
            if root not in order:
                order.append(root)
        for r in self.relationships:
            for n in (r["from"], r["to"]):
                if n not in order:
                    order.append(n)
        for d in self.datasets:
            if d["name"] not in order:
                order.append(d["name"])
        return [by_name[n] for n in order]


# --------------------------------------------------------------------------- data profiling
def profile_csvs(model: "Model", files: dict[str, Path]) -> list[str]:
    """Warn about relationship keys that are not unique on the 'one' side (join fan-out)."""
    import csv
    warnings: list[str] = []
    cache: dict[str, list[dict]] = {}

    def rows(name: str) -> list[dict]:
        if name not in cache:
            with open(files[name], newline="", encoding="utf-8-sig") as fh:
                cache[name] = list(csv.DictReader(fh))
        return cache[name]

    for r in model.relationships:
        if r["to"] not in files or r["from"] not in files:
            continue
        key = r["to_columns"][0]
        dim = rows(r["to"])
        if not dim or key not in dim[0]:
            warnings.append(f"DATA QUALITY: key `{r['to']}.{key}` not found in {files[r['to']].name}")
            continue
        counts: dict[str, int] = {}
        for row in dim:
            counts[row[key]] = counts.get(row[key], 0) + 1
        dup = {k: c for k, c in counts.items() if c > 1}
        if dup:
            fk = r["from_columns"][0]
            hit = sum(1 for row in rows(r["from"]) if row.get(fk) in dup)
            warnings.append(
                f"DATA QUALITY: `{r['to']}.{key}` is not unique ({len(dim)} rows, {len(counts)} "
                f"distinct; {len(dup)} keys repeated). A left join fans out {hit} "
                f"`{r['from']}` rows, so SUM/COUNT measures from `{r['from']}` will be overstated.")
    return warnings


# --------------------------------------------------------------------------- emitter
def build_workbook(model: Model, args) -> tuple[etree._Element, dict]:
    report = {"clean": [], "approximated": [], "dropped": [], "fields": 0, "metrics": 0}
    datasets = model.fact_first()
    ds_id = f"federated.{uuid.uuid4().hex[:28]}"
    conn_id = f"{args.connector}.{uuid.uuid4().hex[:28]}"

    # ---- field naming (dedupe columns that exist in more than one dataset)
    seen: dict[str, int] = {}
    for d in datasets:
        for f in d.get("fields", []):
            seen[f["name"]] = seen.get(f["name"], 0) + 1
    columns: dict[str, str] = {}
    for d in datasets:
        for f in d.get("fields", []):
            dup = seen[f["name"]] > 1 and d is not datasets[0]
            columns[f"{d['name']}.{f['name']}"] = f"{f['name']} ({d['name']})" if dup else f["name"]

    key_cols = set()
    for d in datasets:
        for k in d.get("primary_key", []) or []:
            key_cols.add(f"{d['name']}.{k}")
    for r in model.relationships:
        for c in r.get("from_columns", []):
            key_cols.add(f"{r['from']}.{c}")
        for c in r.get("to_columns", []):
            key_cols.add(f"{r['to']}.{c}")

    # ---- parameters / helper year metrics
    params: list[dict] = []          # {caption, internal, value}
    year_refs: dict[str, str] = {}
    helper_calcs: list[dict] = []
    metric_by_name = {m["name"]: m for m in model.metrics}
    for m in model.metrics:
        sql = (ansi_expr(m) or "").strip()
        if re.fullmatch(r"\d{4}", sql):
            dax = vendor_ext(m, "POWERBI").get("dax", "").strip()
            if dax == sql:  # literal in source too -> a real parameter
                internal = f"Parameter {len(params) + 1}"
                params.append({"caption": titleize(m["name"]), "internal": internal,
                               "value": int(sql), "src": m["name"]})
                year_refs.setdefault(sql, f"[Parameters].[{internal}]")
            else:
                helper_calcs.append(m)
    for m in helper_calcs:
        year_refs.setdefault((ansi_expr(m) or "").strip(), f"[{titleize(m['name'])}]")

    param_by_caption = {p["caption"]: p for p in params}

    def ref_to_tableau(dax: str) -> str | None:
        mm = re.fullmatch(r"\[([^\]]+)\](\s*-\s*\d+)?", dax)
        if not mm:
            return None
        p = param_by_caption.get(mm.group(1))
        base = f"[Parameters].[{p['internal']}]" if p else f"[{mm.group(1)}]"
        return base + (mm.group(2) or "")

    # ---- root
    wb = etree.Element("workbook", {"original-version": TABLEAU_VERSION,
                                    "source-build": SOURCE_BUILD,
                                    "source-platform": "win", "version": TABLEAU_VERSION})
    prefs = etree.SubElement(wb, "preferences")
    etree.SubElement(prefs, "preference", {"name": "ui.encoding.shelf.height", "value": "24"})
    etree.SubElement(prefs, "preference", {"name": "ui.shelf.height", "value": "26"})
    sources = etree.SubElement(wb, "datasources")

    # ---- Parameters datasource
    if params:
        pds = etree.SubElement(sources, "datasource", {"hasconnection": "false", "inline": "true",
                                                        "name": "Parameters", "version": TABLEAU_VERSION})
        etree.SubElement(pds, "aliases", {"enabled": "yes"})
        for p in params:
            c = etree.SubElement(pds, "column", {
                "caption": p["caption"], "datatype": "integer", "name": f"[{p['internal']}]",
                "param-domain-type": "any", "role": "measure", "type": "quantitative",
                "value": str(p["value"])})
            etree.SubElement(c, "calculation", {"class": "tableau", "formula": str(p["value"])})
            report["clean"].append(f"parameter `{p['caption']}` = {p['value']} (from metric `{p['src']}`)")

    # ---- main datasource
    ds = etree.SubElement(sources, "datasource", {
        "caption": titleize(model.name), "inline": "true", "name": ds_id, "version": TABLEAU_VERSION})
    conn = etree.SubElement(ds, "connection", {"class": "federated"})
    etree.SubElement(ds, "aliases", {"enabled": "yes"})
    named = etree.SubElement(conn, "named-connections")
    csv_files: dict[str, Path] = {}
    csv_conn: dict[str, str] = {}
    if args.connector == "csv":
        if not args.csv_dir:
            raise SystemExit("--csv-dir is required with --connector csv")
        for d in datasets:
            hit = next((q for q in Path(args.csv_dir).glob("*.csv")
                        if q.stem.lower() == d["name"].lower()), None)
            if hit is None:
                raise SystemExit(f"no CSV named {d['name']}.csv in {args.csv_dir}")
            csv_files[d["name"]] = hit
            csv_conn[d["name"]] = f"textscan.{uuid.uuid4().hex[:28]}"
            n = etree.SubElement(named, "named-connection",
                                 {"caption": hit.name, "name": csv_conn[d["name"]]})
            etree.SubElement(n, "connection", {
                "class": "textscan", "directory": str(hit.parent).replace("\\", "/"),
                "filename": hit.name, "password": "", "server": ""})
        report["approximated"].extend(profile_csvs(model, csv_files))
    first_src = (datasets[0].get("source") or "").split(".")
    dbname = args.dbname or (first_src[0] if len(first_src) == 3 else "")
    conn_attrs = {"class": args.connector, "server": args.server, "dbname": dbname,
                  "username": args.username or ""}
    if args.port:
        conn_attrs["port"] = str(args.port)
    if args.connector == "sqlserver":  # Microsoft Fabric SQL analytics endpoint
        conn_attrs["odbc-native-protocol"] = "yes"
        # Fabric SQL endpoints reject SQL logins; ask Microsoft's ODBC driver for Entra ID auth.
        if args.auth:
            conn_attrs["odbc-connect-string-extras"] = f"Authentication={args.auth}"
    if args.connector != "csv":
        nc = etree.SubElement(named, "named-connection", {"caption": args.server, "name": conn_id})
        etree.SubElement(nc, "connection", conn_attrs)

    def rel_name(d: dict) -> str:
        return csv_files[d["name"]].name if d["name"] in csv_files else d["name"]

    def table_ref(d: dict) -> str:
        parts = (d.get("source") or d["name"]).split(".")
        if args.schema:
            parts = [args.schema, parts[-1]]
        return ".".join(f"[{p}]" for p in parts[-2:]) if len(parts) >= 2 else f"[{parts[-1]}]"

    def table_rel(d: dict) -> etree._Element:
        if d["name"] in csv_files:
            rel = etree.Element("relation", {
                "connection": csv_conn[d["name"]], "name": rel_name(d),
                "table": f"[{csv_files[d['name']].stem}#csv]", "type": "table"})
            cs = etree.SubElement(rel, "columns", {"character-set": "UTF-8", "header": "yes",
                                                   "locale": "en_US", "separator": ","})
            for i, f in enumerate(d.get("fields", [])):
                tdt = TYPE_MAP.get(f.get("datatype", "String"), ("string", 129, "Count"))[0]
                etree.SubElement(cs, "column", {"datatype": tdt, "name": f["name"],
                                                "ordinal": str(i)})
            return rel
        return etree.Element("relation", {"connection": conn_id, "name": d["name"],
                                          "table": table_ref(d), "type": "table"})

    # join tree: left-deep from the fact
    joined = [datasets[0]["name"]]
    tree = table_rel(datasets[0])
    ds_by_name = {d["name"]: d for d in datasets}
    pending = list(model.relationships)
    progress = True
    while pending and progress:
        progress = False
        for r in list(pending):
            a, b = r["from"], r["to"]
            if (a in joined) == (b in joined):
                continue
            new = b if a in joined else a
            clause_pairs = list(zip(r["from_columns"], r["to_columns"]))
            join = etree.Element("relation", {"join": "left", "type": "join"})
            cl = etree.SubElement(join, "clause", {"type": "join"})
            ex = etree.SubElement(cl, "expression", {"op": "="})
            for fc, tc in clause_pairs[:1]:
                etree.SubElement(ex, "expression", {"op": f"[{rel_name(ds_by_name[r['from']])}].[{fc}]"})
                etree.SubElement(ex, "expression", {"op": f"[{rel_name(ds_by_name[r['to']])}].[{tc}]"})
            for fc, tc in clause_pairs[1:]:
                report["approximated"].append(
                    f"relationship `{r['name']}`: composite key, only first column pair joined")
            join.append(tree)
            join.append(table_rel(ds_by_name[new]))
            tree = join
            joined.append(new)
            pending.remove(r)
            report["clean"].append(f"relationship `{r['name']}` -> left join "
                                   f"{r['from']}.{r['from_columns']} = {r['to']}.{r['to_columns']}")
            progress = True
    for r in pending:
        report["dropped"].append(f"relationship `{r['name']}`: could not be attached to the join tree")
    for d in datasets:
        if d["name"] not in joined:
            tree_rel = table_rel(d)
            report["dropped"].append(f"dataset `{d['name']}` is not related to the fact; "
                                     f"add it manually as a separate relation ({etree.tostring(tree_rel).decode()[:0]})")
    conn.append(tree)

    # cols map + metadata-records
    cols = etree.SubElement(conn, "cols")
    meta = etree.SubElement(conn, "metadata-records")
    for d in datasets:
        for f in d.get("fields", []):
            tname = columns[f"{d['name']}.{f['name']}"]
            etree.SubElement(cols, "map", {"key": f"[{tname}]", "value": f"[{rel_name(d)}].[{f['name']}]"})
            tdt, code, agg = TYPE_MAP.get(f.get("datatype", "String"), ("string", 129, "Count"))
            mr = etree.SubElement(meta, "metadata-record", {"class": "column"})
            for tag, val in [("remote-name", f["name"]), ("remote-type", str(code)),
                             ("local-name", f"[{tname}]"), ("parent-name", f"[{rel_name(d)}]"),
                             ("remote-alias", f["name"]), ("local-type", tdt),
                             ("aggregation", agg), ("contains-null", "true")]:
                etree.SubElement(mr, tag).text = val
    # ---- fields (roles)
    field_defs = []
    for d in datasets:
        for f in d.get("fields", []):
            full = f"{d['name']}.{f['name']}"
            tname = columns[full]
            tdt, _, _ = TYPE_MAP.get(f.get("datatype", "String"), ("string", 129, "Count"))
            is_measure = tdt in ("integer", "real") and full not in key_cols and not (
                f.get("dimension") or {})
            if tdt in ("datetime", "date"):
                role, typ = "dimension", "ordinal"
            elif is_measure:
                role, typ = "measure", "quantitative"
            else:
                role, typ = "dimension", "nominal"
            attrs = {"datatype": tdt, "name": f"[{tname}]", "role": role, "type": typ}
            if tname != f["name"] or f.get("description"):
                attrs["caption"] = tname
            c = etree.SubElement(ds, "column", attrs)
            field_defs.append((tname, tdt, role, typ))
            report["fields"] += 1
    field_cols = {n for n, *_ in field_defs}

    # ---- calculated fields from metrics
    emitted: list[tuple[dict, str, str]] = []  # (metric, caption, formula)
    for m in model.metrics:
        if any(p["src"] == m["name"] for p in params):
            continue
        caption = titleize(m["name"])
        dax = vendor_ext(m, "POWERBI").get("dax", "").strip()
        sql = ansi_expr(m)
        if m in helper_calcs:
            f = ref_to_tableau(dax)
            if f:
                emitted.append((m, caption, f))
                report["clean"].append(f"metric `{m['name']}` -> `{caption}` = {f}")
                continue
        if not sql:
            report["dropped"].append(f"metric `{m['name']}`: no ANSI_SQL expression")
            continue
        t = sql_to_tableau(sql, columns, year_refs)
        if not t.ok:
            report["dropped"].append(f"metric `{m['name']}`: {'; '.join(t.notes)}")
            continue
        notes = list(t.notes)
        approx = "WINDOW_" in t.formula
        if "[CY " in dax and "YEAR(" not in t.formula and "[CY " not in t.formula:
            notes.append("ANSI_SQL expression has no year filter although the source DAX is "
                         "based on a current-year measure -> results differ; fix the Ossie model")
            approx = True
        emitted.append((m, caption, t.formula))
        report["approximated" if approx else "clean"].append(
            f"metric `{m['name']}` -> `{caption}`" + (f" ({'; '.join(notes)})" if notes else ""))

    # compress: replace inlined sub-formulas with references to earlier calcs
    by_formula = {}
    for hm, cap, f in emitted:
        if hm not in helper_calcs:
            by_formula.setdefault(f, cap)
    compressed = []
    for m, cap, f in emitted:
        g = f
        if m in helper_calcs:
            compressed.append((m, cap, g))
            continue
        for other_f, other_cap in sorted(by_formula.items(), key=lambda kv: -len(kv[0])):
            if other_cap == cap or other_f == f or len(other_f) < 12:
                continue
            g = g.replace(other_f, f"[{other_cap}]")
        compressed.append((m, cap, g))

    for m, cap, formula in compressed:
        dt = TYPE_MAP.get(m.get("datatype", "Decimal"), ("real", 5, "Sum"))[0]
        attrs = {"caption": cap, "datatype": dt, "name": f"[{cap}]",
                 "role": "measure", "type": "quantitative"}
        c = etree.SubElement(ds, "column", attrs)
        etree.SubElement(c, "calculation", {"class": "tableau", "formula": formula})
        fmt = vendor_ext(m, "POWERBI").get("formatString")
        if fmt:
            c.set("default-format", fmt)
        report["metrics"] += 1

    # ---- starter worksheet: SUM of the first measure field by a string dimension.
    # Deliberately uses plain fields only, so the view has no calculated-field dependencies.
    # A workbook MUST contain at least one sheet + <windows> entry: Tableau's datasource
    # parser asserts "!m_activeSheet.empty()" (error 501CF476) otherwise.
    sheets = etree.SubElement(wb, "worksheets")
    dim = next((n for n, tdt, role, _ in field_defs if role == "dimension" and tdt == "string"
                and "id" not in n.lower().split("_")), None)
    measure = next((n for n, tdt, role, _ in field_defs if role == "measure" and tdt == "real"),
                   next((n for n, _, role, _ in field_defs if role == "measure"), None))
    sheet = f"{titleize(measure)} by {titleize(dim)}" if dim and measure else "Sheet 1"
    ws = etree.SubElement(sheets, "worksheet", {"name": sheet})
    tbl = etree.SubElement(ws, "table")
    view = etree.SubElement(tbl, "view")
    dss = etree.SubElement(view, "datasources")
    etree.SubElement(dss, "datasource", {"caption": titleize(model.name), "name": ds_id})
    rows = cols_shelf = ""
    if dim and measure:
        dep = etree.SubElement(view, "datasource-dependencies", {"datasource": ds_id})
        mdt = next(t for n, t, *_ in field_defs if n == measure)
        etree.SubElement(dep, "column", {"datatype": "string", "name": f"[{dim}]",
                                         "role": "dimension", "type": "nominal"})
        etree.SubElement(dep, "column", {"datatype": mdt, "name": f"[{measure}]",
                                         "role": "measure", "type": "quantitative"})
        etree.SubElement(dep, "column-instance", {"column": f"[{dim}]", "derivation": "None",
                                                  "name": f"[none:{dim}:nk]", "pivot": "key",
                                                  "type": "nominal"})
        etree.SubElement(dep, "column-instance", {"column": f"[{measure}]", "derivation": "Sum",
                                                  "name": f"[sum:{measure}:qk]", "pivot": "key",
                                                  "type": "quantitative"})
        rows = f"[{ds_id}].[none:{dim}:nk]"
        cols_shelf = f"[{ds_id}].[sum:{measure}:qk]"
    etree.SubElement(view, "aggregation", {"value": "true"})
    etree.SubElement(tbl, "style")
    panes = etree.SubElement(tbl, "panes")
    pane = etree.SubElement(panes, "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
    etree.SubElement(etree.SubElement(pane, "view"), "breakdown", {"value": "auto"})
    etree.SubElement(pane, "mark", {"class": "Automatic"})
    etree.SubElement(tbl, "rows").text = rows
    etree.SubElement(tbl, "cols").text = cols_shelf

    wins = etree.SubElement(wb, "windows", {"source-height": "30"})
    win = etree.SubElement(wins, "window", {"class": "worksheet", "maximized": "true", "name": sheet})
    cards = etree.SubElement(win, "cards")
    left = etree.SubElement(etree.SubElement(cards, "edge", {"name": "left"}), "strip", {"size": "160"})
    for t in ("pages", "filters", "marks"):
        etree.SubElement(left, "card", {"type": t})
    top = etree.SubElement(cards, "edge", {"name": "top"})
    for t, size in (("columns", "2147483647"), ("rows", "2147483647"), ("title", "31")):
        etree.SubElement(etree.SubElement(top, "strip", {"size": size}), "card", {"type": t})
    return wb, report


def write_report(path: Path, model: Model, report: dict, src: Path) -> None:
    lines = [f"# Ossie -> Tableau loss report: `{model.name}`", "",
             f"Source: `{src.as_posix()}`", "",
             f"- Fields emitted: **{report['fields']}**",
             f"- Calculated fields emitted: **{report['metrics']}** of {len(model.metrics)} Ossie metrics",
             f"- Mapped cleanly: **{len(report['clean'])}**  |  approximated: "
             f"**{len(report['approximated'])}**  |  dropped: **{len(report['dropped'])}**", ""]
    for title, key in [("Approximated (review these)", "approximated"),
                       ("Dropped / not translated", "dropped"), ("Mapped cleanly", "clean")]:
        lines += [f"## {title}", ""]
        lines += [f"- {x}" for x in report[key]] or ["- none"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ossie_yaml", type=Path)
    ap.add_argument("out_twb", type=Path)
    ap.add_argument("--connector", default="postgres",
                    help="Tableau connection class: sqlserver (Microsoft Fabric SQL endpoint), "
                         "postgres, snowflake, databricks, ...")
    ap.add_argument("--server", default="localhost",
                    help="Fabric: <id>.datawarehouse.fabric.microsoft.com")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--dbname", default=None, help="Fabric lakehouse/warehouse name")
    ap.add_argument("--schema", default=None, help="override table schema, e.g. dbo")
    ap.add_argument("--csv-dir", default=None, help="folder of <dataset>.csv files (--connector csv)")
    ap.add_argument("--username", default=None, help="Entra ID user principal name")
    ap.add_argument("--auth", default=None,
                    choices=["ActiveDirectoryPassword", "ActiveDirectoryInteractive"],
                    help="Entra ID auth mode for the SQL Server connector (Fabric endpoints)")
    args = ap.parse_args()

    model = Model(yaml.safe_load(args.ossie_yaml.read_text(encoding="utf-8")))
    wb, report = build_workbook(model, args)
    args.out_twb.parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(wb).write(str(args.out_twb), pretty_print=True,
                                xml_declaration=True, encoding="utf-8")
    rp = args.out_twb.with_suffix(".loss_report.md")
    write_report(rp, model, report, args.ossie_yaml)
    print(f"wrote {args.out_twb}\nwrote {rp}")
    print(f"fields={report['fields']} calcs={report['metrics']}/{len(model.metrics)} "
          f"clean={len(report['clean'])} approx={len(report['approximated'])} "
          f"dropped={len(report['dropped'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
