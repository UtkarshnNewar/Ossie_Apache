"""Push an ontology into Databricks, next to the data it describes.

Two things are written into the target schema:

  1. ONTOLOGY TABLES (lossless, queryable with SQL)
        ontology_info           one row: which ontology, from which model, when
        ontology_concepts       every concept: entity or value type, business name, key, source table
        ontology_relationships  every relationship: kind (link / attribute), verbalisation, multiplicity
        ontology_mappings       which table and column each concept / relationship comes from

  2. ANNOTATIONS (native, visible to people and to Genie)
        table comments + tags and column comments carrying the business names

The tables are marked `ossie_hub: ...` in their comment; a same-named table without the marker
is never overwritten unless `overwrite=True`. Nothing runs unless `execute=True`.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

from ossie_hub import ontology as ontology_mod
from ossie_hub.connectors.base import ConnectorError
from ossie_hub.executor import sql_literal
from ossie_hub.targets.planner import Plan, Step

MARK = "ossie_hub:"
TABLES = {
    "ontology_info": ["ontology_name", "description", "source_semantic_model", "pushed_at",
                      "concept_count", "relationship_count"],
    "ontology_concepts": ["concept", "concept_type", "business_name", "extends", "identified_by",
                          "dataset", "description"],
    "ontology_relationships": ["concept", "relationship", "relationship_kind", "related_concept",
                               "multiplicity", "verbalization", "inverse_verbalization",
                               "description"],
    "ontology_mappings": ["concept", "relationship", "kind", "target_concept", "dataset",
                          "column_name", "expression"],
}
TABLE_NOTE = {
    "ontology_info": "which ontology this schema carries",
    "ontology_concepts": "business concepts (entity and value types)",
    "ontology_relationships": "relationships between concepts, incl. fact-to-dimension links",
    "ontology_mappings": "which table and column each concept comes from",
}
EXPR = re.compile(r"^\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")


def humanise(pascal: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", " ", pascal)


def _expr_parts(expression: str) -> tuple[str, str]:
    m = EXPR.match(expression or "")
    return (m.group(1), m.group(2)) if m else ("", "")


def rows_from_ontology(doc: dict) -> dict[str, list[dict]]:
    """Flatten the ontology YAML into the four tables."""
    comps = doc.get("ontology", [])
    types = {c["concept"]: c["type"] for c in comps}
    mapping = (doc.get("ontology_mappings") or [{}])[0]
    cmaps = mapping.get("concept_mappings", [])

    # concept -> source dataset, from its identifier mapping
    dataset_of: dict[str, str] = {}
    mappings: list[dict] = []
    for cm in cmaps:
        c = cm["concept"]
        for om in cm.get("object_mappings", []):
            for ref in om.get("referent_mappings", []):
                ds, col = _expr_parts(ref.get("expression", ""))
                dataset_of.setdefault(c, ds)
                mappings.append({"concept": c, "relationship": ref["relationship"],
                                 "kind": "identifier", "target_concept": c, "dataset": ds,
                                 "column_name": col, "expression": ref.get("expression", "")})

        def walk(children):
            for ch in children or []:
                om = ch.get("object_mapping", {})
                expr = om.get("expression")
                kind = "attribute"
                if expr is None and om.get("referent_mappings"):
                    expr, kind = om["referent_mappings"][0].get("expression", ""), "link"
                ds, col = _expr_parts(expr or "")
                if ch.get("relationship"):
                    mappings.append({"concept": c, "relationship": ch["relationship"], "kind": kind,
                                     "target_concept": om.get("concept", ""), "dataset": ds,
                                     "column_name": col, "expression": expr or ""})
                walk(ch.get("children"))
        for lm in cm.get("link_mappings", []):
            walk(lm.get("children"))

    concepts, relationships = [], []
    for c in comps:
        name = c["concept"]
        concepts.append({
            "concept": name, "concept_type": c["type"], "business_name": humanise(name),
            "extends": ", ".join(c.get("extends", [])), "identified_by": ", ".join(c.get("identify_by", [])),
            "dataset": dataset_of.get(name, ""), "description": c.get("description", "")})
        for r in c.get("relationships", []):
            related = [x["concept"] for x in r.get("roles", [])]
            verb = r.get("verbalizes", [])
            link = any(types.get(x) == "EntityType" for x in related)
            relationships.append({
                "concept": name, "relationship": r["name"],
                "relationship_kind": "link" if link else "attribute",
                "related_concept": ", ".join(related), "multiplicity": r.get("multiplicity", ""),
                "verbalization": verb[0] if verb else "",
                "inverse_verbalization": verb[1] if len(verb) > 1 else "",
                "description": r.get("description", "")})
    info = [{"ontology_name": doc.get("name", ""), "description": doc.get("description", ""),
             "source_semantic_model": (mapping.get("semantic_model") or {}).get("name", ""),
             "pushed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
             "concept_count": str(len(concepts)), "relationship_count": str(len(relationships))}]
    return {"ontology_info": info, "ontology_concepts": concepts,
            "ontology_relationships": relationships, "ontology_mappings": mappings}


def annotation_statements(rows: dict[str, list[dict]], catalog: str, schema: str) -> list[tuple[str, str]]:
    """[(description, SQL)] comments and tags for the data tables and their columns."""
    fq = lambda t: f"`{catalog}`.`{schema}`.`{t}`"
    lit = lambda v: sql_literal(v, "String")
    out: list[tuple[str, str]] = []
    for c in rows["ontology_concepts"]:
        if c["concept_type"] != "EntityType" or not c["dataset"]:
            continue
        text = f"{c['description']}"
        out.append((f"table comment {c['dataset']}", f"COMMENT ON TABLE {fq(c['dataset'])} IS {lit(text)}"))
        out.append((f"table tag {c['dataset']}",
                    f"ALTER TABLE {fq(c['dataset'])} SET TAGS ('ontology_concept' = {lit(c['concept'])})"))
    for m in rows["ontology_mappings"]:
        if not (m["dataset"] and m["column_name"]):
            continue
        if m["kind"] == "link":
            text = (f"Business link: {humanise(m['concept'])} {m['relationship'].replace('_', ' ')} "
                    f"-> {humanise(m['target_concept'])}")
        else:
            text = f"Business name: {humanise(m['target_concept'] or m['concept'])} (of {humanise(m['concept'])})"
        out.append((f"column comment {m['dataset']}.{m['column_name']}",
                    f"ALTER TABLE {fq(m['dataset'])} ALTER COLUMN `{m['column_name']}` COMMENT {lit(text)}"))
    return out


def build_plan(doc: dict, catalog: str, schema: str, annotate: bool = True) -> tuple[Plan, dict, list]:
    rows = rows_from_ontology(doc)
    plan = Plan(target=f"Databricks {catalog}.{schema} (ontology)")
    q = lambda n: f"`{n}`"
    plan.steps.append(Step("create_schema", f"Create schema {catalog}.{schema} if missing",
                           f"CREATE SCHEMA IF NOT EXISTS {q(catalog)}.{q(schema)};"))
    for t, cols in TABLES.items():
        ddl = ", ".join(f"{q(c)} STRING" for c in cols)
        comment = f"{MARK} ontology - {TABLE_NOTE[t]}"
        plan.steps.append(Step(
            "create_table", f"Table {t} ({len(rows[t])} rows)",
            f"CREATE TABLE IF NOT EXISTS {q(catalog)}.{q(schema)}.{q(t)} ({ddl}) USING DELTA "
            f"COMMENT '{comment}';", {"table": t}))
    ann = annotation_statements(rows, catalog, schema) if annotate else []
    if ann:
        plan.steps.append(Step(
            "annotate", f"Comments and tags on the data tables ({len(ann)} statements, best effort; "
                        f"skipped for tables that are not in this schema)", "", {"count": len(ann)}))
    return plan, rows, ann


def run(doc: dict, conn, catalog: str, schema: str, execute: bool = False, overwrite: bool = False,
        annotate: bool = True, log: Callable[[str], None] = print) -> dict:
    bad = ontology_mod.problems(doc)
    if bad:
        raise ConnectorError("the ontology is incomplete, nothing was sent: " + "; ".join(bad))
    plan, rows, ann = build_plan(doc, catalog, schema, annotate)
    q = lambda n: f"`{n}`"
    result: dict = {"executed": execute, "rows": {t: len(r) for t, r in rows.items()},
                    "annotations": len(ann)}

    # ---- read-only preflight: never replace a table that is not ours
    existing: dict[str, str] = {}
    try:
        got = conn.execute(
            f"SELECT table_name, comment FROM `{catalog}`.information_schema.tables "
            f"WHERE table_schema = '{schema}'")["rows"]
        existing = {r[0]: (r[1] or "") for r in got}
    except ConnectorError:
        pass
    foreign = [t for t in TABLES if t in existing and not existing[t].startswith(MARK)]
    if foreign and not overwrite:
        raise ConnectorError(f"refusing to continue: {foreign} already exist in {catalog}.{schema} "
                             f"and were not created by ossie_hub. Choose another schema or pass --overwrite.")
    result["schema_exists"] = bool(existing) or None
    for t in TABLES:
        log(f"   {t:24} {len(rows[t]):>3} rows" + ("   (already exists, will be replaced)"
                                                  if t in existing else ""))
    if annotate:
        log(f"   annotations              {len(ann):>3} comment/tag statements on the data tables")
    if not execute:
        log("DRY RUN: nothing was written.")
        return result

    for step in plan.steps:
        if step.kind in ("create_schema", "create_table"):
            conn.execute(step.sql)
            log(f"-> {step.title}")
    for t, cols in TABLES.items():
        full = f"{q(catalog)}.{q(schema)}.{q(t)}"
        conn.execute(f"TRUNCATE TABLE {full}")
        data = rows[t]
        col_sql = ", ".join(q(c) for c in cols)
        for i in range(0, len(data), 200):
            values = ",".join("(" + ",".join(sql_literal(r.get(c), "String") for c in cols) + ")"
                              for r in data[i:i + 200])
            conn.execute(f"INSERT INTO {full} ({col_sql}) VALUES {values}")
        got = int(conn.execute(f"SELECT COUNT(*) FROM {full}")["rows"][0][0])
        if got != len(data):
            raise ConnectorError(f"row count mismatch for {t}: {got} != {len(data)}")
        log(f"-> {t}: loaded {got} rows")
    # ---- annotations: best effort, a failure never undoes the ontology tables
    done = failed = skipped = 0
    present = {r[0] for r in conn.execute(
        f"SELECT table_name FROM `{catalog}`.information_schema.tables "
        f"WHERE table_schema = '{schema}'")["rows"]}
    for what, sql in ann:
        tbl = re.search(r"`" + re.escape(schema) + r"`\.`([^`]+)`", sql)
        if tbl and tbl.group(1) not in present:
            skipped += 1
            continue
        try:
            conn.execute(sql)
            done += 1
        except ConnectorError as e:
            failed += 1
            result.setdefault("annotation_errors", []).append(f"{what}: {str(e)[:120]}")
    result.update(annotated=done, annotation_failed=failed, annotation_skipped=skipped)
    if annotate:
        log(f"-> annotations: {done} applied, {skipped} skipped (table not in this schema), "
            f"{failed} failed")
    return result
