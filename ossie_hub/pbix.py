"""Read a Power BI .pbix: export its tables to CSV and describe its model as Ossie.

Used when a report's data lives *inside* the file (Import mode, inline/pasted data) so there
is no external source to connect to. Reading uses the open-source `pbixray` library (it can
decode the file's VertiPaq storage); nothing is sent anywhere.

    python -m ossie_hub pbix <file.pbix> --data-dir <folder> --ossie-out <model.yaml>
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from ossie_hub.tmdl import DaxUnsupported, dax_to_sql_notes, slug

AUTO_TABLE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)", re.I)
DTYPE = {"string": "String", "int64": "Integer", "float64": "Float", "double": "Float",
         "datetime64[ns]": "DateTime", "bool": "Boolean", "boolean": "Boolean",
         "decimal": "Decimal"}


# ---- recognise the "previous month" DAX template and express it as a window measure
PM_PATTERN = re.compile(
    r"VAR\s+CurrentYear\s*=\s*MAX\(\s*'?Date'?\[Year\]\s*\)\s*"
    r"VAR\s+CurrentMonth\s*=\s*MAX\(\s*'?Date'?\[Month Number\]\s*\)\s*"
    r"RETURN\s*IF\(\s*CurrentMonth\s*=\s*1\s*,\s*0\s*,\s*COALESCE\(\s*"
    r"CALCULATE\(\s*\[([^\]]+)\]\s*,\s*'?Date'?\[Year\]\s*=\s*CurrentYear\s*,\s*"
    r"'?Date'?\[Month Number\]\s*=\s*CurrentMonth\s*-\s*1\s*\)\s*,\s*0\s*\)\s*\)",
    re.S | re.I)


def reason_for(dax: str, err: str) -> str:
    """The most useful explanation of why a measure could not become model-level SQL."""
    if re.search(r"ALLSELECTED\s*\(", dax):
        return ("ALLSELECTED over a column (MINX/MAXX/AVERAGEX): the value depends on the other "
                "groups in the visual, so it cannot be computed per row of the model")
    if re.search(r"SELECTEDVALUE\s*\(|CONCATENATEX\s*\(|VALUES\s*\(", dax, re.I):
        return "depends on the viewer's selection (SELECTEDVALUE / VALUES / CONCATENATEX)"
    if re.search(r"SWITCH\s*\(", dax, re.I):
        return "presentation logic (SWITCH returning a label or colour)"
    return err


def ident(name: str) -> str:
    """Power BI names (spaces, symbols) -> safe SQL identifiers."""
    return re.sub(r"[^0-9A-Za-z_]+", "_", name).strip("_") or "col"


# names that collide with SQL keywords / functions when used as a table alias (e.g. `date.Date`)
RESERVED = {"date", "time", "timestamp", "user", "table", "order", "group", "select", "from",
            "where", "year", "month", "day", "current_date", "current_timestamp"}


def table_id(name: str) -> str:
    t = ident(name).lower()
    return f"{t}_dim" if t in RESERVED else t


def _fmt(v) -> str:
    """CSV cell text: ISO timestamps, no 'nan'/'<NA>'/'NaT'."""
    import pandas as pd
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return ""
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    import numbers
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, numbers.Integral):          # int, numpy.int64, pandas Int64
        return str(int(v))
    if isinstance(v, numbers.Real):              # float, numpy.float64
        return repr(float(v))
    return str(v)


def inspect_file(path: Path) -> dict:
    """What the .pbix contains, without decoding any data.

    kind == "embedded": the file carries its own data model (Import mode).
    kind == "live"    : a thin report: only a live connection to a model in the Power BI / Fabric
                        service, identified by `model_id`. Nothing to read inside the file.
    """
    import zipfile
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "DataModel" in names:
            return {"kind": "embedded", "size": path.stat().st_size}
        info: dict = {"kind": "unknown", "size": path.stat().st_size}
        if "Connections" in names:
            raw = z.read("Connections")
            for enc in ("utf-8-sig", "utf-16"):
                try:
                    conns = json.loads(raw.decode(enc)).get("Connections", [])
                    break
                except (UnicodeError, json.JSONDecodeError):
                    conns = []
            for c in conns:
                if c.get("ConnectionType") == "pbiServiceLive":
                    info.update(kind="live", model_id=c.get("PbiModelDatabaseName"))
                    m = re.search(r"Initial Catalog=([0-9a-fA-F-]{36})", c.get("ConnectionString", ""))
                    info["model_id"] = info.get("model_id") or (m.group(1) if m else None)
                    break
        return info


def load(path: Path):
    try:
        from pbixray import PBIXRay
    except ImportError as e:  # pragma: no cover
        raise SystemExit("pbixray is required: python -m pip install pbixray") from e
    return PBIXRay(str(path))


def real_tables(model) -> list[str]:
    return [t for t in model.tables if not AUTO_TABLE.match(t)]


def export_csv(model, out_dir: Path) -> dict[str, dict]:
    """Write one CSV per real table (sanitised names). Returns {table: {file, rows, columns}}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for t in real_tables(model):
        df = model.get_table(t)
        df = df.rename(columns={c: ident(c) for c in df.columns})
        path = out_dir / f"{table_id(t)}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(list(df.columns))
            for row in df.itertuples(index=False, name=None):
                w.writerow([_fmt(v) for v in row])
        report[t] = {"file": path.name, "rows": len(df), "columns": list(df.columns)}
    return report


def to_ossie(model, name: str, fact: str | None = None) -> tuple[dict, list[str]]:
    """Describe the model as an Ossie document. Returns (doc, notes)."""
    notes: list[str] = []
    tables = real_tables(model)
    skipped = [t for t in model.tables if t not in tables]
    if skipped:
        notes.append(f"skipped auto-generated date tables: {', '.join(skipped)}")

    datasets = []
    for t in tables:
        cols = model.schema[model.schema["TableName"] == t]
        fields = []
        for _, c in cols.iterrows():
            cid = ident(c["ColumnName"])
            f = {"name": cid, "datatype": DTYPE.get(str(c["PandasDataType"]).lower(), "String"),
                 "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": cid}]}}
            if cid != c["ColumnName"]:
                f["label"] = c["ColumnName"]
            if f["datatype"] in ("DateTime", "Date"):
                f["dimension"] = {"is_time": True}
            fields.append(f)
        datasets.append({"name": table_id(t), "source": f"pbix.{table_id(t)}", "fields": fields,
                         "custom_extensions": [{"vendor_name": "POWERBI",
                                                "data": json.dumps({"original_name": t})}]})

    relationships = []
    for _, r in model.relationships.iterrows():
        a, b = r["FromTableName"], r["ToTableName"]
        if a not in tables or b not in tables:
            continue
        if not int(r["IsActive"]):
            notes.append(f"relationship {a}[{r['FromColumnName']}] -> {b}: inactive, skipped")
            continue
        relationships.append({
            "name": f"{table_id(a)}_to_{table_id(b)}_{ident(r['FromColumnName']).lower()}",
            "from": table_id(a), "to": table_id(b),
            "from_columns": [ident(r["FromColumnName"])], "to_columns": [ident(r["ToColumnName"])],
            "custom_extensions": [{"vendor_name": "POWERBI", "data": json.dumps({
                "cardinality": r["Cardinality"], "crossFilteringBehavior": r["CrossFilteringBehavior"],
                "from_table": a, "from_column": r["FromColumnName"],
                "to_table": b, "to_column": r["ToColumnName"]})}]})
    by = {d["name"]: d for d in datasets}
    for r in relationships:  # the 'one' side key is the table's primary key
        by[r["to"]].setdefault("primary_key", list(r["to_columns"]))

    # ---- measures: translate the ones with a model-level SQL equivalent
    metrics, untranslated, sql_of = [], [], {}
    table_of = {m["Name"]: m["TableName"] for _, m in model.dax_measures.iterrows()}
    pending = [(m["TableName"], m["Name"], m["Expression"])
               for _, m in model.dax_measures.iterrows()]
    colmap = lambda t, c: f"{table_id(t)}.{ident(c)}"
    fact_ids = {r["from"] for r in relationships} - {r["to"] for r in relationships}
    # The fact is the table the measures are written against: the one referenced most often as
    # Table[column]. (Relationships alone are unreliable: a report may relate only slicer tables.)
    dax_texts = [str(m["Expression"]) for _, m in model.dax_measures.iterrows()]
    # A *pure* dimension (only ever on the "one" side, never the "many" side) cannot be the fact.
    pure_dims = {r["to"] for r in relationships} - {r["from"] for r in relationships}
    refs = {t: sum(len(re.findall(r"(?:'" + re.escape(t) + r"'|\b" + re.escape(t) + r")\[", d))
                   for d in dax_texts)
            for t in tables if table_id(t) not in pure_dims}
    ranked = sorted(refs.items(), key=lambda kv: -kv[1])
    fact_orig = None
    if ranked and ranked[0][1] > 0 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        fact_orig = ranked[0][0]
    elif len(fact_ids) == 1:  # no clear leader: fall back to the relationships
        fid = next(iter(fact_ids))
        fact_orig = next((json.loads(d["custom_extensions"][0]["data"]).get("original_name")
                          for d in datasets if d["name"] == fid), None)
    fact_id = table_id(fact_orig) if fact_orig else None
    # the fact column that relates to the Date table: its month is the window's ordering key
    date_rel = next((r for r in relationships if r["to"] == table_id("Date")), None)
    month_field = None
    window_of: dict[str, dict] = {}
    for _ in range(len(pending) + 1):
        progress = False
        for item in list(pending):
            tname, mname, dax = item
            pm = PM_PATTERN.fullmatch(dax.strip()) if date_rel else None
            if pm:
                base = pm.group(1)
                if base not in sql_of:
                    if any(p[1] == base for p in pending):
                        continue  # wait until its base measure is translated
                else:
                    if month_field is None:
                        col = date_rel["from_columns"][0]
                        month_field = re.sub(r"_?Date$", "", col) + "_Month"
                        by[date_rel["from"]]["fields"].append({
                            "name": month_field, "datatype": "DateTime",
                            "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                           "expression": f"DATE_TRUNC('MONTH', {col})"}]},
                            "description": "Month of the date used to relate to the Date table "
                                           "(ordering key for prior-month window measures)."})
                    sql_of[mname] = sql_of[base]
                    window_of[mname] = {"order": month_field, "range": "trailing 1 month",
                                        "semiadditive": "last"}
                    metrics.append({
                        "name": slug(mname),
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql_of[base]}]},
                        "datatype": "Decimal" if "NULLIF" in sql_of[base] else "Integer",
                        "description": f"Previous month's value of '{base}' "
                                       f"(window: trailing 1 month, excludes the current month).",
                        "custom_extensions": [
                            {"vendor_name": "POWERBI", "data": json.dumps(
                                {"dax": dax, "name": mname, "table": tname})},
                            {"vendor_name": "DATABRICKS", "data": json.dumps({"window": [{
                                "order": month_field, "range": "trailing 1 month",
                                "semiadditive": "last"}]})}]})
                    pending.remove(item)
                    progress = True
                    continue
            try:
                sql, caveats = dax_to_sql_notes(dax, sql_of, colmap, fact_table=fact_orig)
            except DaxUnsupported as e:
                if "unknown measure" in str(e) and any(p[1] not in sql_of for p in pending
                                                       if p is not item and p[1] in dax):
                    continue  # depends on a measure that is still pending
                untranslated.append((mname, reason_for(dax, str(e)), dax))
                pending.remove(item)
                progress = True
                continue
            # a measure built on a windowed (prior-month) measure must keep that window,
            # otherwise it would silently return the current month
            wins = {json.dumps(window_of[r]) for r in re.findall(r"\[([^\]]+)\]", dax)
                    if r in window_of}
            if len(wins) > 1:
                untranslated.append((mname, "mixes different windows", dax))
                pending.remove(item)
                progress = True
                continue
            sql_of[mname] = sql
            exts = [{"vendor_name": "POWERBI", "data": json.dumps(
                {"dax": dax, "name": mname, "table": tname})}]
            if wins:
                window_of[mname] = json.loads(next(iter(wins)))
                exts.append({"vendor_name": "DATABRICKS",
                             "data": json.dumps({"window": [window_of[mname]]})})
            metric = {
                "name": slug(mname),
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]},
                "datatype": "Decimal" if "NULLIF" in sql else "Integer",
                "custom_extensions": exts}
            if caveats:
                metric["description"] = "Translated from DAX. Caveat: " + "; ".join(sorted(set(caveats)))
            metrics.append(metric)
            pending.remove(item)
            progress = True
        if not pending or not progress:
            break
    for _, mname, dax in pending:
        untranslated.append((mname, "depends on an untranslated measure", dax))

    ext_data: dict = {}
    if fact_id:
        ext_data["fact_table"] = fact_id   # the planner uses this as the Metric View source
    if untranslated:
        ext_data["untranslated_measures"] = [
            {"name": n, "table": table_of.get(n), "reason": why, "dax": dax}
            for n, why, dax in untranslated]
    model_ext = [{"vendor_name": "POWERBI", "data": json.dumps(ext_data)}] if ext_data else []
    doc_model = {"name": name, "description": f"Imported from Power BI file ({len(tables)} tables).",
                 "datasets": datasets}
    if relationships:
        doc_model["relationships"] = relationships
    if metrics:
        doc_model["metrics"] = metrics
    if model_ext:
        doc_model["custom_extensions"] = model_ext
    for n, why, _ in untranslated:
        notes.append(f"measure '{n}' not translated ({why}); original DAX kept in the model extension")
    return {"version": "0.2.0.dev0", "semantic_model": [doc_model]}, notes
