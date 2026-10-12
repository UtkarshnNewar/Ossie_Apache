"""Make a dimension table's relationship key unique, without silently losing information."""
from __future__ import annotations

EXTRA = "Alternate_Records"


def collapse_duplicates(rows: list[dict], key: str, columns: list[str],
                        keep_variants: bool) -> tuple[list[dict], dict]:
    """Keep the FIRST row per key. When `keep_variants`, the other (differing) rows are
    summarised into `Alternate_Records` so nothing is lost silently.

    Returns (rows, stats) with stats = {"removed": n, "keys_with_variants": n}."""
    seen: dict[str, dict] = {}
    alts: dict[str, list[str]] = {}
    value_cols = [c for c in columns if c != key and c != EXTRA]
    for row in rows:
        k = row.get(key)
        if k not in seen:
            seen[k] = dict(row)
            continue
        other = " / ".join(str(row.get(c, "")) for c in value_cols)
        first = " / ".join(str(seen[k].get(c, "")) for c in value_cols)
        if other != first and other not in alts.setdefault(k, []):
            alts[k].append(other)
    out = []
    for k, row in seen.items():
        if keep_variants:
            row[EXTRA] = " | ".join(alts.get(k, []))
        out.append(row)
    return out, {"removed": len(rows) - len(seen), "keys_with_variants": len(alts)}
