"""Offline migration planner: Ossie model -> ordered, reviewable steps for a target platform.

Nothing here opens a connection. The output is a *plan* (SQL + descriptions) that a later
executor, or a human, can run. The semantic-layer artifact is produced by the repo's own
converters (converters/databricks, converters/snowflake) so behaviour stays consistent.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ossie_hub import document

ROOT = Path(__file__).resolve().parents[2]
# Databricks: the Python converter upstream removed (#333), kept under ossie_hub/vendor.
for sub in ("ossie_hub/vendor", "converters/snowflake/src"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.append(p)

DB_TYPES = {"String": "STRING", "Integer": "BIGINT", "Decimal": "DECIMAL(38,10)",
            "Float": "DOUBLE", "Boolean": "BOOLEAN", "Date": "DATE", "Time": "STRING",
            "DateTime": "TIMESTAMP", "DateTimeTz": "TIMESTAMP", "Opaque": "STRING"}
SF_TYPES = {"String": "VARCHAR", "Integer": "NUMBER(38,0)", "Decimal": "NUMBER(38,10)",
            "Float": "FLOAT", "Boolean": "BOOLEAN", "Date": "DATE", "Time": "TIME",
            "DateTime": "TIMESTAMP_NTZ", "DateTimeTz": "TIMESTAMP_TZ", "Opaque": "VARCHAR"}


@dataclass
class Step:
    kind: str            # create_schema | create_table | copy_data | create_semantic_layer
    title: str
    sql: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    target: str
    steps: list[Step] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)   # filename -> text
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        out = [f"PLAN -> {self.target} (dry run: nothing has been executed)", ""]
        for i, s in enumerate(self.steps, 1):
            out.append(f"{i}. [{s.kind}] {s.title}")
            if s.sql:
                out += ["   " + ln for ln in s.sql.splitlines()]
        if self.artifacts:
            out += ["", "Artifacts: " + ", ".join(self.artifacts)]
        if self.notes:
            out += ["", "Notes:"] + [f"  - {n}" for n in self.notes]
        return "\n".join(out)


# --------------------------------------------------------------------------- shared prep
def is_physical(field_def: dict) -> bool:
    """A field backed by a real column (plain-identifier expression), as opposed to a
    derived dimension such as DATE_TRUNC('MONTH', Created_Date)."""
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", physical_column(field_def)))


def physical_column(field_def: dict) -> str:
    for d in (field_def.get("expression") or {}).get("dialects", []):
        if d.get("dialect") == "ANSI_SQL":
            return d["expression"]
    return field_def["name"]


def table_ddl(doc: dict, type_map: dict[str, str], quote) -> list[tuple[str, str, list[str]]]:
    """[(table_name, create_table_columns_sql, [column names])] from the ORIGINAL model."""
    doc = document.wrap(doc)
    out = []
    for ds in doc["semantic_model"][0]["datasets"]:
        cols = []
        for f in [x for x in ds.get("fields", []) if is_physical(x)]:
            cols.append(f"{quote(physical_column(f))} {type_map.get(f.get('datatype', 'String'), 'STRING')}")
        out.append((ds["name"], ",\n  ".join(cols), [physical_column(f) for f in ds.get("fields", []) if is_physical(f)]))
    return out


def pick_fact(doc: dict) -> str:
    doc = document.wrap(doc)
    m = doc["semantic_model"][0]
    for e in m.get("custom_extensions") or []:  # the importer records which table the measures target
        try:
            hint = json.loads(e.get("data") or "{}").get("fact_table")
        except json.JSONDecodeError:
            hint = None
        if hint and any(d["name"] == hint for d in m["datasets"]):
            return hint
    tos = {r["to"] for r in m.get("relationships", [])}
    for r in m.get("relationships", []):
        if r["from"] not in tos:
            return r["from"]
    return m["datasets"][0]["name"]


def make_names_unique(doc: dict, fact: str | None = None) -> tuple[dict, list[str]]:
    """Flat semantic targets (Databricks Metric Views) need globally unique dimension names.

    The fact table keeps its names; a colliding field on a dimension table is renamed to
    `<dataset>_<name>`. Only the *display name* changes: field expressions, relationship
    columns and keys stay physical column names, so joins and data are untouched."""
    doc = document.wrap(doc)
    doc = copy.deepcopy(doc)
    model = doc["semantic_model"][0]
    notes: list[str] = []
    fact = fact or pick_fact(doc)
    order = sorted(model["datasets"], key=lambda d: d["name"] != fact)  # fact first
    metric_names = {m["name"] for m in model.get("metrics", [])}
    seen: set[str] = set()
    for ds in order:
        for f in ds.get("fields", []):
            original = f["name"]
            if original in seen or original in metric_names:
                f["name"] = f"{ds['name']}_{original}"
                notes.append(f"renamed dimension {ds['name']}.{original} -> {f['name']} "
                             f"(target requires unique dimension names; column unchanged)")
            seen.add(f["name"])
    return doc, notes


def fold_bridges(doc: dict, fact: str, catalog: str, schema: str) -> tuple[dict, list[Step], list[str]]:
    """Databricks Metric Views require every join below a one-to-many join to be one-to-many
    too, so the common Power BI bridge pattern  fact -> bridge -> dimension  is not allowed.

    The fix is to expose the bridge *with* its dimension columns as a flattened VIEW and
    point the Metric View at that view. The normalized tables are kept as they are.
    Returns (model for the converter, view steps, notes)."""
    doc = document.wrap(doc)
    doc = copy.deepcopy(doc)
    model = doc["semantic_model"][0]
    by = {d["name"]: d for d in model["datasets"]}
    rels = model.get("relationships", [])
    steps: list[Step] = []
    notes: list[str] = []
    bridges = sorted({r["from"] for r in rels if r["to"] == fact and r["from"] != fact})
    for b in bridges:
        outs = [r for r in rels if r["from"] == b and r["to"] != fact]
        # only fold a dimension that nothing else uses
        outs = [r for r in outs if all(x is r or r["to"] not in (x["from"], x["to"]) for x in rels)]
        if not outs:
            continue
        taken = {physical_column(f) for f in by[b]["fields"] if is_physical(f)}
        joins, extra_cols, new_fields = [], [], []
        for r in outs:
            dim = by[r["to"]]
            on = " AND ".join(f"b.`{fc}` = `{r['to']}`.`{tc}`"
                              for fc, tc in zip(r["from_columns"], r["to_columns"]))
            joins.append(f"LEFT JOIN `{catalog}`.`{schema}`.`{r['to']}` AS `{r['to']}` ON {on}")
            for f in dim["fields"]:
                if not is_physical(f):
                    continue
                col = physical_column(f)
                if col in r["to_columns"] or col in taken:
                    continue
                alias = col
                taken.add(alias)
                extra_cols.append(f"`{r['to']}`.`{col}` AS `{alias}`")
                nf = copy.deepcopy(f)
                nf["expression"] = {"dialects": [{"dialect": "ANSI_SQL", "expression": alias}]}
                new_fields.append(nf)
            model["datasets"] = [d for d in model["datasets"] if d["name"] != r["to"]]
            rels = [x for x in rels if x is not r]
        view = f"{b}_enriched"
        select = ", ".join(["b.*"] + extra_cols)
        nl = chr(10)
        sql = (f"CREATE OR REPLACE VIEW `{catalog}`.`{schema}`.`{view}` AS{nl}"
               f"SELECT {select}{nl}FROM `{catalog}`.`{schema}`.`{b}` AS b{nl}"
               + nl.join(joins) + ";")
        steps.append(Step("create_view", f"Create view {view} ({b} + "
                          f"{', '.join(r['to'] for r in outs)} columns, for the Metric View)", sql,
                          {"view": view, "bridge": b, "folds": [r["to"] for r in outs]}))
        by[b]["fields"] = by[b]["fields"] + new_fields
        by[b]["source"] = f"{catalog}.{schema}.{view}"
        by[b]["_folded_view"] = True
        notes.append(f"Databricks Metric Views cannot join a dimension below a one-to-many join; "
                     f"exposed {b} + {', '.join(r['to'] for r in outs)} as view `{view}`")
    model["relationships"] = rels
    return doc, steps, notes


def strip_window_features(doc: dict) -> dict:
    """Remove derived dimensions and window measures (they only resolve at the fact grain)."""
    doc = document.wrap(doc)
    doc = copy.deepcopy(doc)
    m = doc["semantic_model"][0]
    for d in m["datasets"]:
        d["fields"] = [f for f in d["fields"] if is_physical(f)]
    def windowed(metric):
        for e in metric.get("custom_extensions") or []:
            if e.get("vendor_name") == "DATABRICKS" and "window" in (e.get("data") or ""):
                return True
        return False
    m["metrics"] = [x for x in m.get("metrics", []) if not windowed(x)]
    return doc


def restrict(doc: dict, keep: set[str]) -> dict:
    """Copy of the model containing only the given datasets and the relationships among them."""
    doc = document.wrap(doc)
    doc = copy.deepcopy(doc)
    m = doc["semantic_model"][0]
    m["datasets"] = [d for d in m["datasets"] if d["name"] in keep]
    m["relationships"] = [r for r in m.get("relationships", [])
                          if r["from"] in keep and r["to"] in keep]
    return doc


def retarget_sources(doc: dict, prefix: str) -> dict:
    doc = document.wrap(doc)
    doc = copy.deepcopy(doc)
    for ds in doc["semantic_model"][0]["datasets"]:
        if ds.pop("_folded_view", False):
            continue  # already points at its flattened view
        ds["source"] = f"{prefix}.{ds['name']}"
    return doc


def run_quiet(fn, *args, **kw) -> tuple[Any, list[str]]:
    """Call a converter, folding its per-field warnings into a short summary."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fn(*args, **kw)
    kinds = Counter()
    for w in caught:
        msg = str(w.message)
        if "custom_extensions" in msg:
            kinds["source-platform metadata (e.g. POWERBI formatString/DAX) not carried to target"] += 1
        elif "is_time" in msg:
            kinds["time-dimension flag has no counterpart in target"] += 1
        else:
            kinds[msg] += 1
    return result, [f"{k} (x{n})" for k, n in kinds.items()]
