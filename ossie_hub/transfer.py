"""Transfers between platforms, shared by the CLI and the web UI.

    fabric     -> databricks : implemented and verified (model + rows + metric view)
    databricks -> fabric     : reads model + rows and shows the Fabric plan (dry run only)

A transfer = read the model as Ossie -> read the rows -> make keys unique in transit ->
create schema/tables/semantic layer on the target -> load -> verify measures.
"""
from __future__ import annotations

import sys
from argparse import Namespace
from dataclasses import dataclass
from typing import Callable

from ossie_hub import document, executor
from ossie_hub.connectors.base import ConnectorError
from ossie_hub.connectors.databricks import DatabricksConnector
from ossie_hub.connectors.fabric import FabricConnector
from ossie_hub.sources import CachedSource, LakehouseSource
from ossie_hub.targets import plan_databricks
from ossie_hub.targets.planner import Plan


def pick(label: str, options: list[dict], given: str | None = None) -> dict:
    """Choose one live option: by flag if given, else ask (numbered list) on a terminal."""
    if not options:
        raise ConnectorError(f"no {label.lower()} available")
    if given:
        hits = [o for o in options if o["name"].lower() == given.lower() or o["id"] == given]
        if len(hits) != 1:
            raise ConnectorError(f"{label} '{given}' matched {len(hits)} of "
                                 f"{[o['name'] for o in options]}")
        return hits[0]
    if not sys.stdin.isatty():
        raise ConnectorError(f"{label} not specified (pass it as a flag when not interactive)")
    print()
    print(f"{label}:")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o['name']}")
    while True:
        raw = input(f"{label} number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]


def ask_text(label: str, given: str | None, default: str | None = None) -> str:
    if given:
        return given
    if sys.stdin.isatty():
        v = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
        return v or (default or "")
    if default:
        return default
    raise ConnectorError(f"{label} not specified")


@dataclass
class Prepared:
    doc: dict
    plan: Plan
    source: CachedSource
    catalog: str
    schema: str
    workspace: str
    model: str
    notes: list[str]


def prepare_fabric_to_databricks(fab: FabricConnector, dbx: DatabricksConnector, o: Namespace,
                                 log: Callable[[str], None] = print) -> Prepared:
    """Read the model and build the plan. Reads only; writes nothing anywhere."""
    ws = pick("Workspace", fab.list_workspaces(), o.workspace)
    model = pick("Semantic model", fab.list_models(ws["id"]), o.model)
    log(f"Reading model '{model['name']}' from Fabric workspace '{ws['name']}'...")
    doc, notes = fab.read_model(workspace=ws["id"], semantic_model=model["name"])

    first = doc["semantic_model"][0]["datasets"][0]["source"].split(".")
    lakehouse = getattr(o, "lakehouse", None) or (first[0] if len(first) == 3 else None)
    lake_schema = getattr(o, "lake_schema", None) or (first[1] if len(first) == 3 else None)
    if not lakehouse:
        raise ConnectorError("cannot infer the lakehouse; pass --lakehouse")
    source = CachedSource(LakehouseSource(fab, ws["id"], lakehouse, lake_schema))

    doc, vnotes = executor.with_variant_columns(doc, source)
    notes += vnotes
    catalog = ask_text("Target catalog", o.catalog, dbx.test()["default_catalog"])
    schema = ask_text("Target schema", o.schema, f"{doc['semantic_model'][0]['name']}_from_fabric")
    plan = plan_databricks(doc, catalog, schema)
    return Prepared(doc, plan, source, catalog, schema, ws["name"], model["name"],
                    notes + plan.notes)


def verify(dbx: DatabricksConnector, prep: Prepared, rows: dict) -> dict[str, dict]:
    view = f"{prep.doc['semantic_model'][0]['name']}_metrics"
    return executor.verify_measures(dbx, prep.catalog, prep.schema, view,
                                    executor.expected_from_rows(prep.doc, rows))


def fabric_to_databricks(fab: FabricConnector, dbx: DatabricksConnector, o: Namespace,
                         execute: bool, overwrite: bool, log: Callable[[str], None] = print) -> int:
    prep = prepare_fabric_to_databricks(fab, dbx, o, log)
    for n in prep.notes:
        log(f"   note: {n}")
    log(prep.plan.render() + "\n")
    res = executor.run_plan(prep.plan, prep.doc, dbx, prep.source, prep.catalog, prep.schema,
                            execute=execute, overwrite=overwrite, log=log)
    if not execute:
        log("\nDry run only. Re-run with --execute to create and load.")
        return 0
    check = verify(dbx, prep, res["rows"])
    log("\nVERIFICATION (Metric View vs. values computed from the source rows):")
    for name, c in check.items():
        log(f"  {'OK ' if c['ok'] else 'BAD'} {name}: expected {c['expected']:,.2f}  got {c['got']:,.2f}")
    return 0 if all(c["ok"] for c in check.values()) else 2


def databricks_to_fabric(dbx: DatabricksConnector, fab: FabricConnector, o: Namespace,
                         execute: bool, log: Callable[[str], None] = print) -> int:
    catalog = ask_text("Source catalog", o.catalog, dbx.test()["default_catalog"])
    schemas = dbx.options("schema", catalog=catalog)
    schema = pick("Source schema", schemas, o.schema)["name"]
    log(f"Reading '{catalog}.{schema}' from Databricks...")
    doc, notes = dbx.read_model(catalog=catalog, schema=schema)
    for n in notes:
        log(f"   note: {n}")
    m = doc["semantic_model"][0]
    ws = pick("Target Fabric workspace", fab.list_workspaces(), o.workspace)
    lines = [f"PLAN -> Microsoft Fabric workspace '{ws['name']}' (dry run)", ""]
    n = 1
    lines.append(f"{n}. [create_lakehouse] Lakehouse '{o.lakehouse or m['name'] + '_lh'}' "
                 f"(schema-enabled)")
    for ds in m["datasets"]:
        n += 1
        lines.append(f"{n}. [write_delta] Delta table Tables/dbo/{ds['name']} "
                     f"({len(ds['fields'])} columns)")
    n += 1
    lines.append(f"{n}. [create_semantic_model] Direct Lake semantic model '{m['name']}' with "
                 f"{len(m.get('relationships', []))} relationships and {len(m.get('metrics', []))} measures")
    log("\n".join(lines))
    if execute:
        raise ConnectorError(
            "Writing to Fabric is not implemented yet: the model and rows can be read from "
            "Databricks, but the Fabric writer (lakehouse tables + semantic model) is the "
            "next piece. Nothing was changed.")
    log("\nDry run only (Fabric writer is not implemented yet).")
    return 0


def publish_databricks_to_fabric(dbx, fab, doc: dict, catalog: str, schema: str, workspace: str,
                                 lakehouse: str, lake_schema: str, model_name: str,
                                 execute: bool, overwrite: bool = False,
                                 log: Callable[[str], None] = print) -> dict:
    """Databricks tables + an Ossie model  ->  Fabric lakehouse schema + Direct Lake model."""
    doc = document.wrap(doc)
    from ossie_hub import fabric_writer, powerbi_tmdl
    from ossie_hub.sources import DatabricksSource

    result: dict = {"executed": execute}
    source = CachedSource(DatabricksSource(dbx, catalog, schema))
    target = fabric_writer.FabricTarget(fab, workspace, lakehouse)
    model = doc["semantic_model"][0]
    datasets = model["datasets"]

    log(f"Reading rows from: {source.label}")
    counts = {d["name"]: len(source.rows(d["name"])) for d in datasets}
    for t, n in counts.items():
        log(f"   {t}: {n} rows")
    existing = target.existing_tables(lake_schema)
    clash = sorted(set(existing) & set(counts))
    prior_model = target.existing_model(model_name)
    parts = powerbi_tmdl.build_parts(doc, model_name, lake_schema, target.ws, target.lh, target.lh_name)
    result.update(counts=counts, existing_tables=existing, parts=[p["path"] for p in parts])

    log("")
    log(f"PLAN -> Fabric workspace '{workspace}', lakehouse '{target.lh_name}' (dry run)" if not execute
        else f"EXECUTING -> Fabric workspace '{workspace}', lakehouse '{target.lh_name}'")
    log(f"  1. Delta tables in Tables/{lake_schema}/: {', '.join(counts)}  ({sum(counts.values())} rows)")
    log(f"  2. Direct Lake semantic model '{model_name}' ({len(parts)} definition files)")
    log("  3. Refresh the model, then verify the measures with DAX")
    if clash and not overwrite:
        raise ConnectorError(f"Tables/{lake_schema} already contains {clash}; choose another "
                             f"schema name or pass --overwrite")
    if prior_model:
        raise ConnectorError(f"a semantic model named '{model_name}' already exists in the "
                             f"workspace; choose another name (existing models are not replaced)")
    if not execute:
        log("\nDry run only: nothing was written.")
        return result

    result["written"] = target.write_tables(lake_schema, datasets, source, overwrite, log)
    created = target.create_model(model_name, parts)
    model_id = created.get("id") or (target.existing_model(model_name) or {}).get("id")
    if not model_id:
        raise ConnectorError("semantic model was created but its id could not be found")
    log(f"-> created semantic model '{model_name}' ({model_id})")
    status = target.refresh_and_wait(model_id)
    log(f"-> refresh (framing): {status}")
    result.update(model_id=model_id, refresh=status)

    from ossie_hub import executor
    rows = {d["name"]: source.rows(d["name"]) for d in datasets}
    by_slug = executor.expected_from_rows(doc, rows)
    names = {}
    for m in model.get("metrics", []):
        e = powerbi_tmdl.ext(m)
        if m["name"] in by_slug and e.get("name"):
            names[e["name"]] = by_slug[m["name"]]
    result["verification"] = fabric_writer.verify_model(target, model_id, names, log)
    return result
