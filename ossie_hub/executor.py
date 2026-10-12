"""Execute a migration Plan on Databricks, then verify it.

Safety rules:
  * nothing runs unless the caller passes execute=True (the CLI needs --execute);
  * existing tables that already hold rows are never touched unless overwrite=True;
  * relationship keys are made unique IN TRANSIT (first row kept, variants recorded), never
    by editing the source;
  * every run ends with measure verification against values computed from the source rows.
"""
from __future__ import annotations

from ossie_hub import document

import re
from datetime import datetime
from typing import Callable

from ossie_hub.clean import EXTRA, collapse_duplicates
from ossie_hub.connectors.base import ConnectorError
from ossie_hub.connectors.databricks import DatabricksConnector
from ossie_hub.sources import RowSource
from ossie_hub.targets.planner import Plan, is_physical, physical_column

BATCH = 400


def sql_literal(value: str | None, datatype: str) -> str:
    if value is None or value == "":
        return "NULL"
    if datatype == "Integer":
        return str(int(float(value)))
    if datatype in ("Float", "Decimal"):
        return repr(float(value))
    if datatype in ("DateTime", "Date"):
        v = value.strip()
        try:
            datetime.fromisoformat(v)
        except ValueError as e:
            raise ConnectorError(f"unparseable timestamp {value!r}") from e
        return f"TIMESTAMP'{v}'" if datatype == "DateTime" else f"DATE'{v[:10]}'"
    if datatype == "Boolean":
        return "TRUE" if value.strip().lower() in ("1", "true", "t", "yes") else "FALSE"
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def one_side_keys(doc: dict) -> dict[str, str]:
    """{dataset: key column} for the 'one' side of every relationship."""
    doc = document.wrap(doc)
    out: dict[str, str] = {}
    for r in doc["semantic_model"][0].get("relationships", []):
        out.setdefault(r["to"], r["to_columns"][0])
    return out


def with_variant_columns(doc: dict, source: RowSource) -> tuple[dict, list[str]]:
    """If a dimension's key repeats in the data, add an `Alternate_Records` column to that
    dataset (copy of the model) so the collapsed variants are kept, not lost."""
    doc = document.wrap(doc)
    import copy
    doc, notes = copy.deepcopy(doc), []
    keys = one_side_keys(doc)
    for ds in doc["semantic_model"][0]["datasets"]:
        if ds["name"] not in keys or any(f["name"] == EXTRA for f in ds["fields"]):
            continue
        values = [r.get(keys[ds["name"]]) for r in source.rows(ds["name"])]
        if len(values) != len(set(values)):
            ds["fields"].append({
                "name": EXTRA, "datatype": "String",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": EXTRA}]},
                "description": "Other records that shared this key in the source "
                               "(collapsed to one row per key)."})
            notes.append(f"{ds['name']}.{keys[ds['name']]} repeats in the source; added column "
                         f"{EXTRA} to keep the collapsed variants")
    return doc, notes


def load_tables(doc: dict, source: RowSource) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Read every dataset and make dimension keys unique. Returns (rows, per-table stats)."""
    doc = document.wrap(doc)
    keys = one_side_keys(doc)
    rows: dict[str, list[dict]] = {}
    stats: dict[str, dict] = {}
    for ds in doc["semantic_model"][0]["datasets"]:
        name = ds["name"]
        data = source.rows(name)
        columns = [physical_column(f) for f in ds["fields"] if is_physical(f)]
        missing = [c for c in columns if c != EXTRA and data and c not in data[0]]
        if missing:
            raise ConnectorError(f"{name}: source is missing columns {missing}")
        stats[name] = {"source_rows": len(data), "collapsed": 0, "variants": 0}
        if name in keys:
            data, s = collapse_duplicates(data, keys[name], columns,
                                          keep_variants=EXTRA in columns)
            stats[name].update(collapsed=s["removed"], variants=s["keys_with_variants"])
        rows[name] = data
    return rows, stats


def run_plan(plan: Plan, doc: dict, conn: DatabricksConnector, source: RowSource,
             catalog: str, schema: str, execute: bool = False, overwrite: bool = False,
             semantic_only: bool = False,
             log: Callable[[str], None] = print) -> dict:
    doc = document.wrap(doc)
    q = lambda n: f"`{n}`"
    datasets = {d["name"]: d for d in doc["semantic_model"][0]["datasets"]}
    result: dict = {"executed": execute, "tables": {}}

    # ---- read-only preflight: what already exists on the target?
    existing: dict[str, int] = {}
    try:
        for r in conn.execute(f"SHOW TABLES IN {q(catalog)}.{q(schema)}")["rows"]:
            n = conn.execute(f"SELECT COUNT(*) FROM {q(catalog)}.{q(schema)}.{q(r[1])}")["rows"][0][0]
            existing[r[1].lower()] = int(n)
    except ConnectorError:
        pass
    clashes = sorted(t for t, n in existing.items() if t in {d.lower() for d in datasets} and n > 0)
    result["existing_tables"] = existing
    if clashes and not overwrite and not semantic_only:
        raise ConnectorError(f"refusing to continue: {clashes} already contain rows in "
                             f"{catalog}.{schema}. Choose another schema or pass --overwrite.")

    log(f"Reading rows from: {source.label}")
    rows, stats = load_tables(doc, source)
    for t, s in stats.items():
        extra = (f"  | made key unique: removed {s['collapsed']} repeated rows"
                 f" ({s['variants']} keys had differing variants)") if s["collapsed"] else ""
        log(f"   {t}: {s['source_rows']} rows read{extra}")
    result["stats"] = stats
    result["rows"] = rows

    if not execute:
        log("DRY RUN: no changes made. Existing target tables: "
            f"{existing or 'none (schema is new or empty)'}")
        return result

    for step in plan.steps:
        if semantic_only and step.kind in ("create_schema", "create_table", "copy_data"):
            continue  # data untouched: only (re)create views / Metric Views
        if step.kind in ("create_schema", "create_table", "create_view", "create_semantic_layer"):
            log(f"-> {step.title}")
            conn.execute(step.sql)
        elif step.kind == "copy_data":
            table = step.detail["table"]
            cols = [(physical_column(f), f.get("datatype", "String"))
                    for f in datasets[table]["fields"] if is_physical(f)]
            full = f"{q(catalog)}.{q(schema)}.{q(table)}"
            conn.execute(f"TRUNCATE TABLE {full}")
            col_sql = ", ".join(q(c) for c, _ in cols)
            data = rows[table]
            for i in range(0, len(data), BATCH):
                values = ",".join("(" + ",".join(sql_literal(r.get(c), dt) for c, dt in cols) + ")"
                                  for r in data[i:i + BATCH])
                conn.execute(f"INSERT INTO {full} ({col_sql}) VALUES {values}")
            got = int(conn.execute(f"SELECT COUNT(*) FROM {full}")["rows"][0][0])
            result["tables"][table] = {"expected_rows": len(data), "loaded_rows": got}
            log(f"-> {table}: loaded {got} of {len(data)} rows")
            if got != len(data):
                raise ConnectorError(f"row count mismatch for {table}: {got} != {len(data)}")
    return result


def _wrapped(e: str) -> bool:
    """True when the whole string is a single parenthesised group or one function call."""
    i = e.find("(")
    if i < 0 or not e.endswith(")"):
        return False
    depth = 0
    for j in range(i, len(e)):
        depth += e[j] == "("
        depth -= e[j] == ")"
        if depth == 0:
            return j == len(e) - 1
    return False


def _split_top(e: str, token: str) -> int | None:
    depth = 0
    for i in range(len(e) - len(token) + 1):
        depth += e[i] == "("
        depth -= e[i] == ")"
        if depth == 0 and e.startswith(token, i):
            return i
    return None


def _num(text: str):
    """Evaluate a numeric literal expression such as `(2023 - 1)`; None if it is not one."""
    if re.fullmatch(r"[0-9\s.+*/()-]+", text):
        return eval(text, {"__builtins__": {}}, {})  # noqa: S307  (digits and operators only)
    return None


def _cond_ok(row: dict, cond: str) -> bool:
    """Evaluate `t.c = 'v'`, `t.c IN ('a','b')`, `EXTRACT(YEAR FROM t.c) = n` joined by AND."""
    for part in re.split(r"\s+AND\s+", cond.strip()):
        m = re.fullmatch(r"EXTRACT\((YEAR|MONTH|DAY) FROM \w+\.(\w+)\)\s*=\s*(.+)", part.strip(), re.S)
        if m:
            unit, col, rhs = m.groups()
            text = (row.get(col) or "").strip()
            want = _num(rhs.strip())
            if len(text) < 10 or want is None:
                return False
            part_of = {"YEAR": text[0:4], "MONTH": text[5:7], "DAY": text[8:10]}[unit]
            if int(part_of) != int(want):
                return False
            continue
        m = re.fullmatch(r"\w+\.(\w+)\s+IN\s+\((.*)\)", part.strip(), re.S)
        if m:
            allowed = {v.strip().strip("'") for v in m.group(2).split(",")}
            if (row.get(m.group(1)) or "") not in allowed:
                return False
            continue
        m = re.fullmatch(r"\w+\.(\w+)\s*=\s*'(.*)'", part.strip(), re.S)
        if not m:
            raise ValueError(f"unsupported filter: {part}")
        if (row.get(m.group(1)) or "") != m.group(2):
            return False
    return True


def _eval(expr: str, rows: dict[str, list[dict]]):
    """Tiny evaluator for the SQL shapes the translator emits. Raises ValueError otherwise."""
    e = expr.strip()
    while e.startswith("(") and _wrapped(e):
        e = e[1:-1].strip()
    if e.startswith("COALESCE(") and _wrapped(e) and e.endswith(", 0)"):
        v = _eval(e[len("COALESCE("):-4], rows)
        return 0.0 if v is None else v
    lit = _num(e)
    if lit is not None:
        return float(lit)
    for op in (" - ", " + "):  # additive, left-associative: split at the right-most top-level op
        cut_at = None
        depth = 0
        for i in range(len(e) - len(op) + 1):
            depth += e[i] == "("
            depth -= e[i] == ")"
            if depth == 0 and e.startswith(op, i):
                cut_at = i
        if cut_at is not None and " / NULLIF(" not in e[:cut_at].split("(")[0]:
            a, b = _eval(e[:cut_at], rows), _eval(e[cut_at + len(op):], rows)
            if a is None or b is None:
                return None
            return a - b if op == " - " else a + b
    cut = _split_top(e, " / NULLIF(")
    if cut is not None and e.endswith(", 0)"):
        a = _eval(e[:cut], rows)
        b = _eval(e[cut + len(" / NULLIF("):-4], rows)
        return None if not b else a / b
    m = re.fullmatch(r"(SUM|AVG|COUNT)\((DISTINCT\s+)?(\w+)\.(\w+)\)"
                     r"(?:\s+FILTER\s*\(WHERE\s+(.+)\))?", e, re.S)
    if not m or m.group(3) not in rows:
        raise ValueError(f"unsupported expression: {e[:60]}")
    fn, distinct, table, col, cond = m.groups()
    data = [r for r in rows[table] if cond is None or _cond_ok(r, cond)]
    vals = [r[col] for r in data if r.get(col) not in (None, "")]
    if fn == "COUNT":
        return float(len(set(vals)) if distinct else len(vals))
    if not vals:
        return None
    nums = [float(v) for v in vals]
    return round(sum(nums), 2) if fn == "SUM" else sum(nums) / len(nums)


def expected_from_rows(doc: dict, rows: dict[str, list[dict]]) -> dict[str, float]:
    """Independent check values computed straight from the source rows (not from the target).
    Measures whose SQL shape the evaluator does not know are skipped."""
    doc = document.wrap(doc)
    out: dict[str, float] = {}
    for m in doc["semantic_model"][0].get("metrics", []):
        if any(e.get("vendor_name") == "DATABRICKS" and "window" in (e.get("data") or "")
               for e in m.get("custom_extensions") or []):
            continue  # windowed (prior-period) measures need a per-month check, not a grand total
        sql = next((d["expression"] for d in m["expression"]["dialects"]
                    if d["dialect"] == "ANSI_SQL"), "")
        try:
            v = _eval(sql, rows)
        except (ValueError, KeyError, ZeroDivisionError):
            continue
        if v is not None:
            out[m["name"]] = v
    return out


def verify_measures(conn: DatabricksConnector, catalog: str, schema: str, view: str,
                    measures: dict[str, float], tol: float = 0.01) -> dict[str, dict]:
    out = {}
    for name, expected in measures.items():
        got = float(conn.execute(
            f"SELECT MEASURE(`{name}`) FROM `{catalog}`.`{schema}`.`{view}`")["rows"][0][0])
        out[name] = {"expected": expected, "got": got, "ok": abs(got - expected) <= tol}
    return out
