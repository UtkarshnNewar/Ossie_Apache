"""Ossie model -> Power BI / Fabric semantic model definition (TMDL, Direct Lake).

The repo has no Ossie -> Power BI converter, so this emitter is part of Ossie Hub. Its layout
follows an existing Direct Lake model from the target tenant (definition.pbism, model.tmdl,
expressions.tmdl, tables/*.tmdl, relationships.tmdl, .platform).

Original Power BI names (tables / columns / measures) are restored from the POWERBI
extensions the .pbix importer stored, so the original DAX works unchanged. Physical
Delta names stay sanitised and are bound through `sourceColumn` / `entityName`.
"""
from __future__ import annotations

from ossie_hub import document

import base64
import json
import re
import uuid
from typing import Any

NS = uuid.UUID("5b0f6e0a-9b0e-4c55-9a53-2f8a0a1c7d11")
TMDL_TYPES = {"String": "string", "Integer": "int64", "Float": "double", "Decimal": "decimal",
              "DateTime": "dateTime", "Date": "dateTime", "Boolean": "boolean"}
T = "\t"


def q(name: str) -> str:
    """TMDL identifier: quote when it is not a plain word."""
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "'" + name.replace("'", "''") + "'"


def gid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(parts)))


def ext(node: dict, vendor: str = "POWERBI") -> dict[str, Any]:
    for e in node.get("custom_extensions") or []:
        if e.get("vendor_name") == vendor:
            try:
                return json.loads(e.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def dax_block(name: str, dax: str, indent: int = 1) -> list[str]:
    """`measure Name = <dax>` (single line) or the multi-line form with an indented body."""
    pad = T * indent
    lines = [ln.rstrip() for ln in dax.strip().splitlines()]
    if len(lines) == 1:
        return [f"{pad}measure {q(name)} = {lines[0].strip()}"]
    # keep relative indentation of the original body, normalised to a common minimum
    nonblank = [ln for ln in lines if ln.strip()]
    base = min(len(ln) - len(ln.lstrip()) for ln in nonblank[1:] or nonblank)
    body = []
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        lead = len(ln) - len(ln.lstrip())
        rel = 0 if i == 0 else max(lead - base, 0)
        body.append(f"{pad}{T}{T}" + (" " * rel) + ln.strip())
    return [f"{pad}measure {q(name)} ="] + body


def build_parts(doc: dict, display_name: str, schema: str, workspace_id: str, lakehouse_id: str,
                lakehouse_name: str, only_measures_named: set[str] | None = None) -> list[dict]:
    """Return the `definition.parts` for the Fabric create-semantic-model call."""
    doc = document.wrap(doc)
    model = doc["semantic_model"][0]
    expr_name = f"DirectLake - {lakehouse_name}"
    datasets = model["datasets"]
    orig_table = {d["name"]: ext(d).get("original_name", d["name"]) for d in datasets}

    # ---- measures grouped by the table that hosts them
    measures: list[dict] = []
    for mt in model.get("metrics", []):
        e = ext(mt)
        if e.get("dax"):
            measures.append({"name": e.get("name", mt["name"]), "table": e.get("table"),
                             "dax": e["dax"], "doc": mt.get("description")})
    for u in ext({"custom_extensions": model.get("custom_extensions")}).get("untranslated_measures", []):
        measures.append({"name": u["name"], "table": u.get("table"), "dax": u["dax"], "doc": None})
    if only_measures_named is not None:
        measures = [m for m in measures if m["name"] in only_measures_named]
    fact_orig = orig_table.get(datasets[0]["name"], datasets[0]["name"])
    by_table: dict[str, list[dict]] = {}
    known = set(orig_table.values())
    for m in measures:
        by_table.setdefault(m["table"] if m["table"] in known else fact_orig, []).append(m)

    parts: list[dict] = []

    def add(path: str, text: str) -> None:
        parts.append({"path": path, "payload": b64(text), "payloadType": "InlineBase64"})

    add("definition.pbism", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/"
                   "definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}}, indent=2) + "\n")
    add(".platform", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
                   "platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": display_name},
        "config": {"version": "2.0", "logicalId": "00000000-0000-0000-0000-000000000000"}}, indent=2) + "\n")
    add("definition/database.tmdl", "database\n\tcompatibilityLevel: 1606\n")

    refs = "".join(f"ref table {q(orig_table[d['name']])}\n" for d in datasets)
    add("definition/model.tmdl",
        "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tsourceQueryCulture: en-US\n\tdataAccessOptions\n\t\tlegacyRedirects\n"
        "\t\treturnErrorValuesAsNull\n\n"
        "annotation __PBI_TimeIntelligenceEnabled = 1\n\n"
        f"annotation PBI_QueryOrder = [\"{expr_name}\"]\n\n"
        "annotation PBI_ProTooling = [\"DirectLakeOnOneLakeInWeb\",\"WebModelingEdit\"]\n\n"
        + refs)
    add("definition/expressions.tmdl",
        f"expression {q(expr_name)} =\n\t\tlet\n\t\t    Source = AzureStorage.DataLake("
        f"\"https://onelake.dfs.fabric.microsoft.com/{workspace_id}/{lakehouse_id}\", "
        f"[HierarchicalNavigation=true])\n\t\tin\n\t\t    Source\n"
        f"\tlineageTag: {gid('expr', expr_name)}\n\n"
        "\tannotation PBI_IncludeFutureArtifacts = False\n")

    # ---- tables
    for d in datasets:
        t_orig = orig_table[d["name"]]
        lines = [f"table {q(t_orig)}",
                 f"{T}lineageTag: {gid('table', t_orig)}",
                 f"{T}sourceLineageTag: [{schema}].[{d['name']}]", ""]
        for m in by_table.get(t_orig, []):
            if m["doc"]:
                lines.append(f"{T}/// {m['doc']}")
            lines += dax_block(m["name"], m["dax"])
            lines += [f"{T}{T}lineageTag: {gid('measure', t_orig, m['name'])}", ""]
        for f in d["fields"]:
            expr_text = next((x["expression"] for x in f["expression"]["dialects"]), f["name"])
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr_text):
                continue  # derived dimension (e.g. DATE_TRUNC): not a physical column
            c_orig = f.get("label") or f["name"]
            phys = next((x["expression"] for x in f["expression"]["dialects"]), f["name"])
            dtype = TMDL_TYPES.get(f.get("datatype", "String"), "string")
            lines += [f"{T}column {q(c_orig)}", f"{T}{T}dataType: {dtype}"]
            if dtype == "int64":
                lines.append(f"{T}{T}formatString: 0")
            if dtype == "dateTime":
                lines.append(f"{T}{T}formatString: General Date")
            lines += [f"{T}{T}lineageTag: {gid('column', t_orig, c_orig)}",
                      f"{T}{T}sourceLineageTag: {phys}",
                      f"{T}{T}summarizeBy: none",
                      f"{T}{T}sourceColumn: {phys}", "",
                      f"{T}{T}annotation SummarizationSetBy = Automatic", ""]
        lines += [f"{T}partition {q(t_orig)} = entity",
                  f"{T}{T}mode: directLake",
                  f"{T}{T}source",
                  f"{T}{T}{T}entityName: {d['name']}",
                  f"{T}{T}{T}schemaName: {schema}",
                  f"{T}{T}{T}expressionSource: {q(expr_name)}", ""]
        add(f"definition/tables/{t_orig}.tmdl", "\n".join(lines) + "\n")

    # ---- relationships
    rel_lines = []
    for r in model.get("relationships", []):
        e = ext(r)
        ft = e.get("from_table") or orig_table[r["from"]]
        tt = e.get("to_table") or orig_table[r["to"]]
        fc = e.get("from_column") or r["from_columns"][0]
        tc = e.get("to_column") or r["to_columns"][0]
        rel_lines += [f"relationship {gid('rel', ft, fc, tt, tc)}"]
        if str(e.get("crossFilteringBehavior", "")).lower().startswith("both"):
            rel_lines.append(f"{T}crossFilteringBehavior: bothDirections")
        rel_lines += [f"{T}fromColumn: {q(ft)}.{q(fc)}", f"{T}toColumn: {q(tt)}.{q(tc)}", ""]
    if rel_lines:
        add("definition/relationships.tmdl", "\n".join(rel_lines) + "\n")
    return parts
