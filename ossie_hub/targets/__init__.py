"""Target-platform planners (offline)."""
from __future__ import annotations

import yaml

from ossie_hub import document
from ossie_hub.targets import planner as _p
from ossie_hub.targets.planner import Plan, Step


def _drop_unsafe_keys(doc: dict, unsafe: dict[str, str] | None) -> tuple[dict, list[str]]:
    """Remove primary keys the data contradicts, so converters do not assert uniqueness."""
    doc = document.wrap(doc)
    import copy
    doc, notes = copy.deepcopy(doc), []
    for ds in doc["semantic_model"][0]["datasets"]:
        if ds["name"] in (unsafe or {}):
            ds.pop("primary_key", None)
            notes.append(f"DATA QUALITY: {unsafe[ds['name']]}; key assertion withheld "
                         f"(no rely.at_most_one_match). Clean the data before relying on totals.")
    return doc, notes


def plan_databricks(doc: dict, catalog: str, schema: str, fact: str | None = None,
                    unsafe_keys: dict[str, str] | None = None) -> Plan:
    from ossie_databricks.ossie_to_metric_view import convert_ossie_to_metric_view
    plan = Plan(target=f"Databricks Unity Catalog {catalog}.{schema}")
    q = lambda n: f"`{n}`"
    plan.steps.append(Step("create_schema", f"Create schema {catalog}.{schema} if missing",
                           f"CREATE SCHEMA IF NOT EXISTS {q(catalog)}.{q(schema)};"))
    for table, cols, names in _p.table_ddl(doc, _p.DB_TYPES, q):
        full = f"{q(catalog)}.{q(schema)}.{q(table)}"
        plan.steps.append(Step("create_table", f"Create table {table}",
                               f"CREATE TABLE IF NOT EXISTS {full} (\n  {cols}\n) USING DELTA;",
                               {"table": table, "columns": names}))
        plan.steps.append(Step("copy_data", f"Copy rows of {table} from the source platform",
                               detail={"table": table, "mode": "append-after-truncate",
                                       "status": "needs a source+target connection to execute"}))
    safe, key_notes = _drop_unsafe_keys(doc, unsafe_keys)
    plan.notes += key_notes
    fact = fact or _p.pick_fact(safe)
    folded, view_steps, fold_notes = _p.fold_bridges(safe, fact, catalog, schema)
    plan.steps += view_steps
    plan.notes += fold_notes
    model_name = folded["semantic_model"][0]["name"]
    all_names = {d["name"] for d in folded["semantic_model"][0]["datasets"]}
    bridges = [s.detail["bridge"] for s in view_steps]

    def emit(sub_doc: dict, fact_name: str, view_name: str, what: str) -> None:
        """Convert one model slice to a Metric View and add its step (never aborts the plan)."""
        flat, rename_notes = _p.make_names_unique(sub_doc, fact_name)
        flat = _p.retarget_sources(flat, f"{catalog}.{schema}")
        try:
            yaml_text, warn = _p.run_quiet(convert_ossie_to_metric_view,
                                           yaml.safe_dump(document.flatten(flat), sort_keys=False),
                                           source=fact_name)
        except Exception as e:  # e.g. ConversionError: report it, keep the rest of the plan
            plan.notes.append(f"Metric View {view_name} NOT generated: {e}")
            return
        plan.notes += [f"[{view_name}] {n}" for n in rename_notes + warn]
        plan.artifacts[f"{view_name}.yaml"] = yaml_text
        ddl = (f"CREATE OR REPLACE VIEW {q(catalog)}.{q(schema)}.{q(view_name)}\n"
               f"WITH METRICS LANGUAGE YAML AS $$\n{yaml_text.rstrip()}\n$$;")
        plan.steps.append(Step("create_semantic_layer", f"Create Metric View {view_name} ({what})", ddl,
                               {"view": view_name, "fact": fact_name}))

    # main view: the fact grain, with every many-to-one dimension (no one-to-many branches)
    emit(_p.restrict(folded, all_names - set(bridges)), fact, f"{model_name}_metrics",
         f"one row per {fact}")
    # one view per bridge: dimensions that fan out are only valid at the bridge's own grain
    for b in bridges:
        emit(_p.strip_window_features(_p.restrict(folded, (all_names - set(bridges)) | {b})), b,
             f"{model_name}_by_{b}",
             f"one row per {b}, for slicing by its columns")
    return plan


def plan_snowflake(doc: dict, database: str, schema: str,
                   unsafe_keys: dict[str, str] | None = None) -> Plan:
    doc = document.wrap(doc)
    from ossie_snowflake.ossie_to_snowflake import convert_ossie_to_snowflake
    plan = Plan(target=f"Snowflake {database}.{schema}")
    q = lambda n: n  # unquoted -> upper-cased, matching the Cortex converter's output
    plan.steps.append(Step("create_schema", f"Create schema {database}.{schema} if missing",
                           f"CREATE SCHEMA IF NOT EXISTS {database}.{schema};"))
    for table, cols, names in _p.table_ddl(doc, _p.SF_TYPES, q):
        plan.steps.append(Step("create_table", f"Create table {table}",
                               f"CREATE TABLE IF NOT EXISTS {database}.{schema}.{table} (\n  {cols}\n);",
                               {"table": table, "columns": names}))
        plan.steps.append(Step("copy_data", f"Copy rows of {table} from the source platform",
                               detail={"table": table, "mode": "append-after-truncate",
                                       "status": "needs a source+target connection to execute"}))
    safe, key_notes = _drop_unsafe_keys(doc, unsafe_keys)
    plan.notes += key_notes
    retargeted = _p.retarget_sources(safe, f"{database}.{schema}")
    yaml_text, warn = _p.run_quiet(convert_ossie_to_snowflake, yaml.safe_dump(document.flatten(retargeted),
                                                     sort_keys=False))
    plan.notes += warn
    name = doc["semantic_model"][0]["name"]
    plan.artifacts[f"{name}_cortex_semantic_model.yaml"] = yaml_text
    stage = f"{database}.{schema}.SEMANTIC_MODELS"
    plan.steps.append(Step("create_stage", "Create stage for semantic model files",
                           f"CREATE STAGE IF NOT EXISTS {stage} DIRECTORY = (ENABLE = TRUE);"))
    plan.steps.append(Step(
        "create_semantic_layer", f"Upload Cortex Analyst semantic model for {name}",
        f"PUT file://{name}_cortex_semantic_model.yaml @{stage} AUTO_COMPRESS=FALSE OVERWRITE=TRUE;",
        {"stage": stage}))
    return plan


PLANNERS = {"databricks": plan_databricks, "snowflake": plan_snowflake}
