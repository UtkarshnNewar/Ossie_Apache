"""Build a standalone HTML dashboard from a Databricks schema made by ossie_hub.

    python tools/build_dashboard.py --connection dbx-main --catalog workspace \
        --schema sales_dashboard_fabric --out output_models/Sales_Dashboard_fabric/dashboard.html

What it reads (read-only SQL, nothing is written to Databricks):
  * the data tables  orders / customers / products / location   (the data)
  * the Metric View's measures and their headline values         (the semantic model)
  * the ontology_* tables pushed by `ontology push`              (the business meaning)

The result is ONE self-contained .html file (no internet, no libraries): charts, filters and an
"Ontology" tab that shows what each number and each label means in business words.
The data inside is a snapshot taken when the file is built; rebuild to refresh.

The page layout expects the Sales shape (orders + customers + products + location). If a table or
column is missing the script stops and says which, instead of producing a wrong page.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ossie_hub import connections  # noqa: E402
from ossie_hub.connectors.base import ConnectorError  # noqa: E402

REQUIRED = {
    "orders": ["Order_ID", "Order_Date", "Customer_ID", "Segment", "Postal_Code", "Product_ID",
               "Sales", "Profit", "Quantity", "Discount"],
    "customers": ["Customer_ID", "Customer_Name"],
    "products": ["Product_ID", "Category", "Sub_Category"],
    "location": ["Postal_Code", "City", "State", "Region"],
}
ONTOLOGY_TABLES = ["ontology_info", "ontology_concepts", "ontology_relationships", "ontology_mappings"]


def humanise(text: str) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", " ", text)
    return text.replace("_", " ").strip()


def f(v):
    return None if v in (None, "") else float(v)


def build(conn, catalog: str, schema: str, metric_view: str | None, title: str | None) -> dict:
    q = lambda sql: conn.execute(sql)["rows"]
    fq = f"`{catalog}`.`{schema}`"

    present = {}
    for t, c in q(f"SELECT table_name, column_name FROM `{catalog}`.information_schema.columns "
                  f"WHERE table_schema = '{schema}'"):
        present.setdefault(t, set()).add(c)
    for t, cols in REQUIRED.items():
        miss = [c for c in cols if c not in present.get(t, set())]
        if miss:
            raise ConnectorError(f"{catalog}.{schema}.{t} is missing {miss}; this dashboard expects "
                                 f"the Sales shape (orders, customers, products, location)")

    # ---- the data: one row per order line, dimensions already joined
    n_expected = int(q(f"SELECT COUNT(*) FROM {fq}.orders")[0][0])
    rows = q(f"""SELECT year(o.Order_Date) * 100 + month(o.Order_Date), o.Order_ID, o.Customer_ID,
                        cu.Customer_Name, p.Category, p.Sub_Category, l.Region, l.State, o.Segment,
                        o.Sales, o.Profit, o.Quantity, o.Discount
                 FROM {fq}.orders o
                 JOIN {fq}.customers cu ON o.Customer_ID = cu.Customer_ID
                 JOIN {fq}.products p ON o.Product_ID = p.Product_ID
                 JOIN {fq}.location l ON o.Postal_Code = l.Postal_Code""")
    if len(rows) != n_expected:
        raise ConnectorError(f"read {len(rows)} rows but orders has {n_expected}: a join dropped or "
                             f"duplicated rows, so the dashboard would be wrong")

    dicts = {k: {} for k in ("ord", "cust", "cat", "sub", "reg", "st", "seg")}
    names: dict[str, str] = {}

    def code(kind, value):
        d = dicts[kind]
        return d.setdefault(value, len(d))

    packed = []
    for ym, order, cid, cname, cat, sub, reg, st, seg, sales, profit, qty, disc in rows:
        names[cid] = cname
        packed.append([int(ym), code("ord", order), code("cust", cid), code("cat", cat), code("sub", sub),
                       code("reg", reg), code("st", st), code("seg", seg),
                       round(f(sales) or 0, 2), round(f(profit) or 0, 2), int(float(qty or 0)),
                       round(f(disc) or 0, 4)])
    listed = {k: [v for v, _ in sorted(d.items(), key=lambda kv: kv[1])] for k, d in dicts.items()}
    sub_cat = {}
    for r in rows:
        sub_cat[r[5]] = r[4]
    out = {
        "rows": packed,
        "dict": {"cust": [names[c] for c in listed["cust"]], "cat": listed["cat"], "sub": listed["sub"],
                 "reg": listed["reg"], "st": listed["st"], "seg": listed["seg"]},
        "sub_cat": {str(dicts["sub"][s]): dicts["cat"][c] for s, c in sub_cat.items()},
        "order_count": len(listed["ord"]),
    }

    # ---- the semantic model: Metric View measures + their headline values
    if not metric_view:
        views = [r[0] for r in q(f"SELECT table_name FROM `{catalog}`.information_schema.tables "
                                 f"WHERE table_schema = '{schema}' AND table_type = 'METRIC_VIEW'")]
        metric_view = views[0] if views else None
    measures, headline = [], {}
    if metric_view:
        text = next((r[1] for r in q(f"DESCRIBE TABLE EXTENDED {fq}.`{metric_view}`") if r[0] == "View Text"), "")
        spec = yaml.safe_load(text) if text else {}
        for m in spec.get("measures", []):
            measures.append({"name": m["name"], "expr": str(m.get("expr", "")), "comment": m.get("comment", "")})
        if measures:
            sel = ", ".join(f"MEASURE(`{m['name']}`)" for m in measures)
            vals = q(f"SELECT {sel} FROM {fq}.`{metric_view}`")[0]
            headline = {m["name"]: f(v) for m, v in zip(measures, vals)}
    out["metric_view"] = metric_view
    out["measures"] = measures
    out["headline"] = headline

    # ---- the ontology layer
    onto = None
    if all(t in present for t in ONTOLOGY_TABLES) or all(
            any(r[0] == t for r in q(f"SELECT table_name FROM `{catalog}`.information_schema.tables "
                                     f"WHERE table_schema = '{schema}'")) for t in ONTOLOGY_TABLES):
        def tab(name, cols):
            data = q(f"SELECT {', '.join(cols)} FROM {fq}.`{name}`")
            return [dict(zip(cols, [("" if v is None else v) for v in r])) for r in data]
        onto = {
            "info": tab("ontology_info", ["ontology_name", "description", "source_semantic_model", "pushed_at"])[0],
            "concepts": tab("ontology_concepts", ["concept", "concept_type", "business_name", "identified_by",
                                                  "dataset", "description"]),
            "relationships": tab("ontology_relationships", ["concept", "relationship", "relationship_kind",
                                                            "related_concept", "multiplicity", "verbalization"]),
            "mappings": tab("ontology_mappings", ["concept", "relationship", "kind", "target_concept",
                                                  "dataset", "column_name"]),
        }
        # column -> business term (for chart chips and measure definitions)
        terms = {}
        for m in onto["mappings"]:
            if m["column_name"] and m["kind"] in ("attribute", "identifier") and m["dataset"]:
                terms[f"{m['dataset']}.{m['column_name']}".lower()] = {
                    "term": humanise(m["target_concept"]), "entity": humanise(m["concept"]),
                    "source": f"{m['dataset']}.{m['column_name']}"}
        onto["terms"] = terms
        tables = {"orders": "orders"}
        for ms in out["measures"]:
            cols = re.findall(r"[A-Za-z_]\w*", ms["expr"])
            ms["terms"] = sorted({terms[k]["term"] for c in cols for k in (f"orders.{c}".lower(),) if k in terms})
    out["ontology"] = onto
    out["meta"] = {
        "title": title or f"{humanise(schema)} - business view",
        "catalog": catalog, "schema": schema, "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "rows": len(packed), "years": sorted({p[0] // 100 for p in packed}),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--connection", required=True, help="saved Databricks connection")
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--schema", required=True)
    ap.add_argument("--metric-view", help="default: the first Metric View in the schema")
    ap.add_argument("--title", help="page title")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    try:
        data = build(connections.connect(args.connection), args.catalog, args.schema, args.metric_view, args.title)
    except ConnectorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    template = (Path(__file__).with_name("dashboard_template.html")).read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    html = template.replace("/*__DATA__*/null", payload).replace("__TITLE__", data["meta"]["title"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(html) / 1024:.0f} KB, {data['meta']['rows']} order lines, "
          f"{len(data['measures'])} measures, ontology: {'yes' if data['ontology'] else 'not found'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
