"""Ossie document shapes.

Apache Ossie (since upstream #383 / #396) defines ONE semantic model per document, with
`name`, `datasets`, `relationships`, `metrics` at the root next to `version` (the *flat* form).
Older files, and ossie_hub's own in-memory model, use a wrapper:

    {"version": ..., "semantic_model": [ {name, datasets, ...} ]}

ossie_hub keeps the wrapped shape inside the program and converts at the edges:

    load()   reads either form from disk            -> wrapped
    dump()   writes a model to disk                 -> flat (what the upstream validator expects)
    flatten() / wrap()  the two conversions on dicts
"""
from __future__ import annotations

from pathlib import Path

import yaml

VERSION = "0.2.0.dev0"


def is_wrapped(doc: dict) -> bool:
    return isinstance(doc, dict) and "semantic_model" in doc and "datasets" not in doc


def wrap(doc: dict) -> dict:
    """Any Ossie document -> the wrapped shape used inside ossie_hub (no-op if already)."""
    if is_wrapped(doc):
        return doc
    model = {k: v for k, v in doc.items() if k != "version"}
    return {"version": doc.get("version", VERSION), "semantic_model": [model]}


def flatten(doc: dict) -> dict:
    """Any Ossie document -> the flat shape (no-op if already). Only the first model is kept."""
    if not is_wrapped(doc):
        return doc
    models = doc["semantic_model"]
    if len(models) != 1:
        raise ValueError(f"expected exactly one semantic model, found {len(models)}; "
                         f"split the document into one file per model")
    return {"version": doc.get("version", VERSION), **models[0]}


def load(path: Path) -> dict:
    """Read an Ossie semantic model file in either shape; returns the wrapped shape."""
    return wrap(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def dump(doc: dict, header: str = "") -> str:
    """YAML text of a model in the flat shape (ASCII-safe)."""
    return header + yaml.safe_dump(flatten(doc), sort_keys=False, allow_unicode=False)
