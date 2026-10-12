"""Light data profiling used to keep generated models honest."""
from __future__ import annotations

from ossie_hub import document

import csv
from pathlib import Path


def nonunique_keys(doc: dict, csv_dir: Path) -> dict[str, str]:
    """{dataset: reason} for relationship 'one'-side keys that are not unique in the data."""
    doc = document.wrap(doc)
    model = doc["semantic_model"][0]
    files = {p.stem.lower(): p for p in Path(csv_dir).glob("*.csv")}
    out: dict[str, str] = {}
    for r in model.get("relationships", []):
        ds, key = r["to"], r["to_columns"][0]
        path = files.get(ds.lower())
        if path is None or ds in out:
            continue
        with open(path, newline="", encoding="utf-8-sig") as fh:
            values = [row.get(key) for row in csv.DictReader(fh)]
        distinct = len(set(values))
        if distinct != len(values):
            out[ds] = (f"{ds}.{key} is not unique ({len(values)} rows, {distinct} distinct); "
                       f"joins would fan out")
    return out
