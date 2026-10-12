"""Write everything a conversion produces, and check it.

One call produces, next to each other:

    <name>_ossie.yaml                the semantic model
    <name>_ontology.yaml             the business ontology derived from it
    <name>_translation_report.md     which measures became SQL, which stayed DAX, and why

and validates the first two against the repo schemas.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

from ossie_hub import document
from ossie_hub import ontology as ontology_mod

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "validation" / "validate.py"
ONTOLOGY_SCHEMA = ROOT / "ontology" / "ontology.json"


def validate_file(path: Path, ontology: bool = False) -> tuple[bool, str]:
    """Run the repo's own validator. Returns (passed, message)."""
    cmd = [sys.executable, str(VALIDATOR), str(path)]
    if ontology:
        cmd += ["--schema", str(ONTOLOGY_SCHEMA)]
    run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (run.stdout or "") + (run.stderr or "")
    passed = "Validation PASSED" in text
    if passed:
        return True, "PASSED"
    lines = [ln.strip() for ln in text.splitlines()
             if "[Schema]" in ln or "FAILED" in ln or "Error" in ln]
    return False, "; ".join(lines[:3]) or text.strip()[-300:]


def _ext(node: dict, vendor: str) -> dict:
    for e in node.get("custom_extensions") or []:
        if e.get("vendor_name") == vendor:
            try:
                return json.loads(e.get("data") or "{}")
            except json.JSONDecodeError:
                return {}
    return {}


def coverage(doc: dict, notes: list[str] | None = None) -> dict:
    """{translated: [...], kept_as_dax: [(name, reason)], total}"""
    doc = document.wrap(doc)
    model = doc["semantic_model"][0]
    translated = []
    for m in model.get("metrics", []):
        ext = _ext(m, "POWERBI")
        translated.append({
            "name": ext.get("name", m["name"]),
            "sql": next((d["expression"] for d in m["expression"]["dialects"]), ""),
            "windowed": "window" in _ext(m, "DATABRICKS"),
            "caveat": m.get("description", "") if str(m.get("description", "")).startswith("Translated") else "",
        })
    kept = [(u["name"], u.get("reason", "")) for u in
            _ext({"custom_extensions": model.get("custom_extensions")}, "POWERBI").get("untranslated_measures", [])]
    if not kept and notes:  # Fabric/TMDL path reports untranslated measures through notes
        for n in notes:
            m = re.match(r"measure `(.+?)`: NOT translated \((.*?)\);", n)
            if m:
                kept.append((m.group(1), m.group(2)))
    return {"translated": translated, "kept_as_dax": kept,
            "total": len(translated) + len(kept)}


def measure_report(doc: dict, source_name: str, notes: list[str] | None = None) -> str:
    doc = document.wrap(doc)
    cov = coverage(doc, notes)
    t, k, n = len(cov["translated"]), len(cov["kept_as_dax"]), cov["total"]
    pct = f"{100 * t / n:.0f}%" if n else "n/a"
    out = [f"# Measure translation report: {doc['semantic_model'][0]['name']}", "",
           f"Source: `{source_name}`", "",
           f"- Translated to SQL: **{t} of {n}** ({pct})",
           f"- Kept as the original DAX only: **{k}**", "",
           "Rules: a measure is translated only when its meaning is expressible as a model-level "
           "SQL aggregate. Anything else is **rejected with a reason, never guessed**, and its "
           "original DAX stays in the model (`custom_extensions`).", ""]
    if cov["translated"]:
        out += ["## Translated", "", "| Measure | SQL (ANSI) | Notes |", "|---|---|---|"]
        for m in cov["translated"]:
            sql = m["sql"].replace("|", "\\|")
            sql = sql if len(sql) <= 110 else sql[:107] + "..."
            note = "prior-month window (Databricks extension)" if m["windowed"] else ""
            if m["caveat"]:
                note = (note + "; " if note else "") + m["caveat"].replace("Translated from DAX. Caveat: ", "")
            out.append(f"| {m['name']} | `{sql}` | {note.replace('|', '/')} |")
        out.append("")
    if cov["kept_as_dax"]:
        out += ["## Kept as DAX only", "", "| Measure | Why |", "|---|---|"]
        out += [f"| {name} | {reason.replace('|', '/')} |" for name, reason in cov["kept_as_dax"]]
        out.append("")
    return "\n".join(out)


def write_all(doc: dict, ossie_out: Path, source_name: str, header: str = "",
              notes: list[str] | None = None, make_ontology: bool = True,
              ontology_out: Path | None = None, make_report: bool = True) -> dict:
    """Write model (+ ontology + report) and validate. Returns a summary dict."""
    ossie_out.parent.mkdir(parents=True, exist_ok=True)
    ossie_out.write_text(document.dump(doc, header),
                         encoding="utf-8")
    result: dict = {"ossie": ossie_out}
    result["ossie_valid"] = validate_file(ossie_out)
    if make_ontology:
        onto = ontology_out or ontology_mod.ontology_path_for(ossie_out)
        text = ontology_mod.render(doc, ossie_out.name)
        onto.write_text(text, encoding="utf-8")
        result["ontology"] = onto
        result["ontology_summary"] = ontology_mod.summary(text)
        result["ontology_valid"] = validate_file(onto, ontology=True)
    if make_report:
        rep = ossie_out.with_name(ossie_out.stem.removesuffix("_ossie") + "_translation_report.md")
        rep.write_text(measure_report(doc, source_name, notes), encoding="utf-8")
        result["report"] = rep
    result["coverage"] = coverage(doc, notes)
    return result


def print_summary(res: dict, log=print) -> None:
    log("")
    log(f"Ossie model : {res['ossie']}   validation: {res['ossie_valid'][1]}")
    if "ontology" in res:
        log(f"Ontology    : {res['ontology']}   validation: {res['ontology_valid'][1]}")
        log(f"              {res['ontology_summary']}")
    if "report" in res:
        log(f"Measures    : {res['report']}")
    c = res["coverage"]
    t, k = len(c["translated"]), len(c["kept_as_dax"])
    log(f"              {t} of {c['total']} translated to SQL, {k} kept as DAX only")
    if c["total"] and t / c["total"] < 0.5:
        log("WARNING     : fewer than half of the measures were translated; "
            "open the translation report before relying on the metrics")
    for name, reason in c["kept_as_dax"]:
        log(f"   - {name}: {reason[:150]}")
