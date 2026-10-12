"""Convert a Power BI / Fabric TMDL semantic model into an Apache Ossie YAML file.

Usage:
    python tools/tmdl_to_ossie.py Knowledge/salesDashboard_datasource.md \
        output_models/fabric_sales_ossie.yaml --name sales [--infer-relationships]

Reads the text produced by Fabric's "TMDL View -> Script TMDL" (tables, columns, measures,
partitions and, if present, relationships). DAX measures are translated to ANSI SQL for the
subset of DAX that has a direct SQL equivalent; the original DAX is always preserved in a
POWERBI custom extension. Anything that cannot be translated is listed in the report and
skipped rather than emitted wrong.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from ossie_hub import document

DATATYPE = {
    "string": "String", "int64": "Integer", "double": "Float", "decimal": "Decimal",
    "datetime": "DateTime", "boolean": "Boolean",
}


# --------------------------------------------------------------------------- TMDL parsing
def unquote(name: str) -> str:
    name = name.strip()
    if len(name) >= 2 and name[0] == name[-1] == "'":
        return name[1:-1].replace("''", "'")
    return name


class Node:
    def __init__(self, kind: str, name: str, indent: int, doc: str = "", expr: str = ""):
        self.kind, self.name, self.indent, self.doc, self.expr = kind, name, indent, doc, expr
        self.props: dict[str, str] = {}
        self.children: list[Node] = []


def parse_tmdl(text: str) -> list[Node]:
    """Indentation-based TMDL parser (tabs or 4-space indents). Returns top-level objects."""
    top: list[Node] = []
    stack: list[Node] = []
    doc_buf: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip():
            continue
        expanded = raw.replace("\t", "    ")
        indent = (len(expanded) - len(expanded.lstrip())) // 4
        line = expanded.strip()
        if line.startswith("///"):
            doc_buf.append(line[3:].strip())
            continue
        if line == "createOrReplace" or line.startswith("ref "):
            continue
        while stack and stack[-1].indent >= indent:
            stack.pop()

        m = re.match(r"^(table|column|measure|partition|relationship|hierarchy|role|expression|"
                     r"calculationGroup|annotation|culture|perspective)\s+(.*)$", line)
        if m and not (stack and stack[-1].kind == "source"):
            kind, rest = m.group(1), m.group(2)
            expr = ""
            if kind in ("measure", "column", "partition", "expression", "annotation") and "=" in rest:
                head, _, tail = rest.partition("=")
                rest, expr = head.strip(), tail.strip()
                if kind == "measure" and not expr:  # multi-line / fenced expression
                    block = []
                    while i < len(lines) and (lines[i].strip() == "" or
                                              (len(lines[i].replace("\t", "    ")) -
                                               len(lines[i].replace("\t", "    ").lstrip())) // 4 > indent + 0):
                        if re.match(r"^\s*[A-Za-z]+:\s", lines[i]) and "```" not in "".join(block):
                            break
                        block.append(lines[i].strip())
                        i += 1
                    expr = " ".join(b for b in block if b and b != "```")
            node = Node(kind, unquote(rest), indent, " ".join(doc_buf), expr)
            doc_buf = []
            (stack[-1].children if stack else top).append(node)
            stack.append(node)
            continue

        doc_buf = []
        if line == "source" and stack:
            node = Node("source", "source", indent)
            stack[-1].children.append(node)
            stack.append(node)
            continue
        pm = re.match(r"^([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if pm and stack:
            stack[-1].props[pm.group(1)] = pm.group(2).strip()
    return top


# DAX -> SQL lives in ossie_hub/dax.py (re-exported here for existing imports)
from ossie_hub.dax import (DaxParser, DaxUnsupported, apply_filter, dax_to_sql,  # noqa: E402,F401
                           dax_to_sql_notes, paren)


# --------------------------------------------------------------------------- build Ossie
def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.replace("%", " pct ").lower()).strip("_")


def build(nodes: list[Node], model_name: str, infer: bool) -> tuple[dict, list[str]]:
    notes: list[str] = []
    tables = [n for n in nodes if n.kind == "table"]
    rels = [n for n in nodes if n.kind == "relationship"]
    for t in tables:
        rels += [c for c in t.children if c.kind == "relationship"]

    datasets, fact_candidates = [], []
    measures_sql: dict[str, str] = {}
    measure_nodes: list[tuple[str, Node]] = []

    for t in tables:
        entity = schema = lake = None
        for p in (c for c in t.children if c.kind == "partition"):
            src = next((c for c in p.children if c.kind == "source"), None)
            if src:
                entity = src.props.get("entityName")
                schema = src.props.get("schemaName")
                lake = unquote(src.props.get("expressionSource", "")).replace("DirectLake - ", "")
        source = ".".join(x for x in (lake, schema, entity or t.name) if x)
        if not entity:
            notes.append(f"table `{t.name}`: no entity partition found; source set to `{source}`")

        fields = []
        for c in (c for c in t.children if c.kind == "column"):
            dt = DATATYPE.get(c.props.get("dataType", "string").lower(), "Opaque")
            col = c.props.get("sourceColumn", c.name)
            f = {"name": c.name,
                 "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": col}]},
                 "datatype": dt}
            if dt in ("DateTime", "Date"):
                f["dimension"] = {"is_time": True}
            if c.doc:
                f["description"] = c.doc
            ext = {k: c.props[k] for k in ("summarizeBy", "formatString") if k in c.props}
            if ext:
                f["custom_extensions"] = [{"vendor_name": "POWERBI", "data": json.dumps(ext)}]
            fields.append(f)

        for m in (c for c in t.children if c.kind == "measure"):
            measure_nodes.append((t.name, m))
        if any(c.kind == "measure" for c in t.children):
            fact_candidates.append(t.name)

        ds = {"name": t.name, "source": source, "fields": fields}
        if t.doc:
            ds["description"] = t.doc
        datasets.append(ds)

    # ---- relationships (authoritative from TMDL, else optional inference)
    relationships = []
    for r in rels:
        if r.props.get("isActive", "true").lower() == "false":
            notes.append(f"relationship `{r.name}` is inactive in the source model; skipped")
            continue
        fc, tc = r.props.get("fromColumn", ""), r.props.get("toColumn", "")
        fm = re.match(r"^'?(.+?)'?\.'?(.+?)'?$", fc)
        tm = re.match(r"^'?(.+?)'?\.'?(.+?)'?$", tc)
        if not (fm and tm):
            notes.append(f"relationship `{r.name}`: could not parse columns")
            continue
        rel = {"name": f"{fm.group(1)}_to_{tm.group(1)}".lower(),
               "from": fm.group(1), "to": tm.group(1),
               "from_columns": [fm.group(2)], "to_columns": [tm.group(2)]}
        relationships.append(rel)
    if not relationships:
        if infer and fact_candidates:
            fact = fact_candidates[0]
            fact_cols = {f["name"] for d in datasets if d["name"] == fact for f in d["fields"]}
            for d in datasets:
                if d["name"] == fact:
                    continue
                shared = [f["name"] for f in d["fields"] if f["name"] in fact_cols]
                if shared:
                    relationships.append({
                        "name": f"{fact}_to_{d['name']}", "from": fact, "to": d["name"],
                        "from_columns": [shared[0]], "to_columns": [shared[0]],
                        "custom_extensions": [{"vendor_name": "POWERBI", "data": json.dumps(
                            {"inferred": True,
                             "reason": "matching column name; relationship not present in TMDL"})}]})
                    notes.append(f"relationship `{fact}` -> `{d['name']}` on `{shared[0]}` was "
                                 f"INFERRED (script the Model tab to get the real definition)")
                else:
                    notes.append(f"table `{d['name']}`: no shared column with fact `{fact}`, left unrelated")
        else:
            notes.append("no relationships in the TMDL; tables are unrelated "
                         "(script the Model tab, or use --infer-relationships)")
    by_name = {d["name"]: d for d in datasets}
    for r in relationships:
        pk = by_name.get(r["to"])
        if pk is not None and "primary_key" not in pk:
            pk["primary_key"] = list(r["to_columns"])

    # ---- measures: resolve in dependency order so [Measure] refs inline correctly
    metrics, pending = [], list(measure_nodes)
    for _ in range(len(pending) + 1):
        progress = False
        for item in list(pending):
            tname, m = item
            try:
                sql = dax_to_sql(m.expr, measures_sql)
            except DaxUnsupported as e:
                if "unknown measure" in str(e):
                    continue  # maybe defined later
                notes.append(f"measure `{m.name}`: NOT translated ({e}); DAX: {m.expr}")
                pending.remove(item)
                progress = True
                continue
            measures_sql[m.name] = sql
            upper = sql.upper()
            dt = "Integer" if "COUNT(" in upper and "NULLIF" not in upper else "Decimal"
            ext = {"dax": m.expr}
            if "formatString" in m.props:
                ext["formatString"] = m.props["formatString"]
            metric = {"name": slug(m.name),
                      "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]},
                      "datatype": dt,
                      "custom_extensions": [{"vendor_name": "POWERBI", "data": json.dumps(ext)}]}
            if m.doc:
                metric["description"] = m.doc
            metrics.append(metric)
            pending.remove(item)
            progress = True
        if not pending or not progress:
            break
    for _, m in pending:
        notes.append(f"measure `{m.name}`: NOT translated (unresolved reference); DAX: {m.expr}")

    model = {"name": model_name, "description": f"Converted from Fabric semantic model TMDL ({model_name}).",
             "datasets": datasets}
    if relationships:
        model["relationships"] = relationships
    if metrics:
        model["metrics"] = metrics
    return {"version": "0.2.0.dev0", "semantic_model": [model]}, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tmdl", type=Path)
    ap.add_argument("out_yaml", type=Path)
    ap.add_argument("--name", default="sales")
    ap.add_argument("--infer-relationships", action="store_true",
                    help="when the TMDL has no relationships, join the fact table to tables "
                         "sharing a column name (flagged INFERRED)")
    args = ap.parse_args()

    nodes = parse_tmdl(args.tmdl.read_text(encoding="utf-8"))
    doc, notes = build(nodes, args.name, args.infer_relationships)
    args.out_yaml.parent.mkdir(parents=True, exist_ok=True)
    header = ("# yaml-language-server: $schema=../core-spec/ossie-schema.json\n"
              f"# Generated by tools/tmdl_to_ossie.py from {args.tmdl.name}\n")
    args.out_yaml.write_text(document.dump(doc, header),
                             encoding="utf-8")
    sm = doc["semantic_model"][0]
    print(f"wrote {args.out_yaml}: {len(sm['datasets'])} datasets, "
          f"{len(sm.get('relationships', []))} relationships, {len(sm.get('metrics', []))} metrics")
    for n in notes:
        print("NOTE:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
