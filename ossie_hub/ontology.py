"""Derive an Apache Ossie *ontology* from an Ossie semantic model.

    python tools/ossie_to_ontology.py Knowledge/sales_dashboard_ossie.yaml \
        Knowledge/sales_dashboard_ontology.yaml

What is derived from the semantic model
  * one EntityType per dataset (fact and dimensions), identified by its primary key
  * one ValueType per attribute, with a business name
  * one relationship from the fact to each dimension (ManyToOne), taken from the model's
    `relationships`, verbalised in both directions
  * ontology_mappings that bind every concept / relationship back to the dataset fields

Business names are a *vocabulary decision*, so they are kept in the two small tables below
(and fall back to a humanised field name). Edit them and re-run to rename anything.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import yaml

# dataset name -> business concept name (the entity)
ENTITY_NAMES = {
    "orders": "SalesTransaction",   # one row per retail sales line (revenue, profit, quantity)
    "products": "Product",
    "customers": "Customer",
    "location": "Location",
}

# (dataset, field) -> (value type business name, relationship name on the entity)
FIELD_NAMES = {
    ("orders", "row_id"): ("TransactionId", "id"),
    ("orders", "order_id"): ("OrderNumber", "order_number"),
    ("orders", "order_date"): ("OrderDate", "ordered_on"),
    ("orders", "ship_date"): ("ShipDate", "shipped_on"),
    ("orders", "ship_mode"): ("ShipMode", "ship_mode"),
    ("orders", "segment"): ("CustomerSegment", "segment"),
    ("orders", "postal_code"): ("PostalCode", "postal_code"),
    ("orders", "sales"): ("SalesAmount", "sales_amount"),
    ("orders", "quantity"): ("QuantitySold", "quantity"),
    ("orders", "discount"): ("DiscountRate", "discount"),
    ("orders", "profit"): ("ProfitAmount", "profit"),
    ("products", "product_id"): ("ProductId", "id"),
    ("products", "category"): ("ProductCategory", "category"),
    ("products", "sub_category"): ("ProductSubCategory", "sub_category"),
    ("products", "product_name"): ("ProductName", "name"),
    ("customers", "customer_id"): ("CustomerId", "id"),
    ("customers", "customer_name"): ("CustomerName", "name"),
    ("location", "postal_code"): ("PostalCode", "id"),
    ("location", "city"): ("City", "city"),
    ("location", "state"): ("State", "state"),
    ("location", "region"): ("SalesRegion", "region"),
    ("location", "country_region"): ("Country", "country"),
}

# (fact dataset, dimension dataset) -> relationship name and its two verbalisations
LINKS = {
    ("orders", "products"): ("for_product",
                             "{SalesTransaction} is for {Product}",
                             "{Product} is sold in {SalesTransaction}"),
    ("orders", "customers"): ("placed_by",
                              "{SalesTransaction} is placed by {Customer}",
                              "{Customer} places {SalesTransaction}"),
    ("orders", "location"): ("delivered_to",
                             "{SalesTransaction} is delivered to {Location}",
                             "{Location} receives {SalesTransaction}"),
}

BASE_TYPE = {"String": "String", "Integer": "Integer", "Decimal": "Decimal", "Float": "Float",
             "DateTime": "DateTime", "Date": "Date", "Boolean": "Boolean"}


def pascal(text: str) -> str:
    return "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", text) if w)


def snake(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def entity_name(ds: str) -> str:
    d = ds.lower()
    return ENTITY_NAMES.get(d, pascal(d[:-1] if d.endswith("s") else d))


def field_names(ds: str, field: str) -> tuple[str, str]:
    """Business value-type name and relationship name; matching ignores letter case."""
    return FIELD_NAMES.get((ds.lower(), field.lower()),
                           (pascal(f"{entity_name(ds)} {field}"), snake(field)))


def synonyms(node: dict) -> list[str]:
    ctx = node.get("ai_context")
    return list(ctx.get("synonyms", [])) if isinstance(ctx, dict) else []


def column(field: dict) -> str:
    for d in (field.get("expression") or {}).get("dialects", []):
        if d.get("dialect") == "ANSI_SQL":
            return d["expression"]
    return field["name"]


def key_of(name: str, ds: dict) -> str | None:
    """Declared primary key; if the model declares none (e.g. a Fabric fact table), the field
    the business vocabulary calls the identifier."""
    pk = (ds.get("primary_key") or [None])[0]
    if pk is not None:
        return pk
    for f in ds.get("fields", []):
        if FIELD_NAMES.get((name.lower(), f["name"].lower()), (None, None))[1] == "id":
            return f["name"]
    # unknown vocabulary: a column that looks like an identifier, else the first column
    names = [f["name"] for f in ds.get("fields", [])]
    for n in names:
        if re.fullmatch(r"(?i)(id|key|.*[_ ]?(id|key|code|number|no))", n):
            return n
    return names[0] if names else None


def build(doc: dict) -> dict:
    from ossie_hub import document
    doc = document.wrap(doc)   # accepts the flat or the wrapped shape
    model = doc["semantic_model"][0]
    datasets = {d["name"]: d for d in model["datasets"]}
    rels = model.get("relationships", [])
    facts = {r["from"] for r in rels} - {r["to"] for r in rels}
    fact = next(iter(facts)) if facts else model["datasets"][0]["name"]
    dims = [r["to"] for r in rels if r["from"] == fact]
    order = [fact] + [d for d in datasets if d != fact]

    value_types: dict[str, dict] = {}
    entities: list[dict] = []
    fk_columns = {(r["from"], c) for r in rels for c in r["from_columns"]}

    for name in order:
        ds = datasets[name]
        ent = entity_name(name)
        pk = key_of(name, ds)
        is_fact = name in facts or name == fact
        role = "fact" if is_fact else "dimension"
        syn = synonyms(ds)
        base = ds.get("description", "").strip().rstrip(".")
        desc = (f"Business name: {re.sub(r'(?<!^)(?=[A-Z])', ' ', ent)}."
                + (f" {base}." if base else "")
                + (f" Also known as: {', '.join(syn)}." if syn else "")
                + f" ({role}; source dataset '{name}')")
        concept: dict = {"concept": ent, "type": "EntityType", "description": desc}
        relationships: list[dict] = []
        identify = None

        for f in ds["fields"]:
            vt, rel_name = field_names(name, f["name"])
            if (name, f["name"]) in fk_columns:
                continue  # foreign keys become entity-to-entity relationships below
            base = BASE_TYPE.get(f.get("datatype", "String"), "String")
            fsyn = synonyms(f)
            vt_desc = f.get("description") or (f"Also known as: {', '.join(fsyn)}" if fsyn else None)
            value_types.setdefault(vt, {
                "concept": vt, "type": "ValueType", "extends": [base],
                **({"description": vt_desc} if vt_desc else {})})
            is_key = f["name"] == pk
            relationships.append({
                "name": rel_name,
                "roles": [{"concept": vt}],
                "verbalizes": [f"{{{ent}}} is identified by {{{vt}}}" if is_key
                               else f"{{{ent}}} has {{{vt}}}"],
                "multiplicity": "OneToOne" if is_key else "ManyToOne",
            })
            if is_key:
                identify = rel_name

        if rels:
            for r in rels:
                if r["from"] != name:
                    continue
                dim_ent = entity_name(r["to"])
                nm, v1, v2 = LINKS.get((name.lower(), r["to"].lower()), (
                    f"has_{snake(dim_ent)}", f"{{{ent}}} has {{{dim_ent}}}",
                    f"{{{dim_ent}}} is referenced by {{{ent}}}"))
                cols = ", ".join(f"{name}.{a} = {r['to']}.{b}"
                                 for a, b in zip(r["from_columns"], r["to_columns"]))
                relationships.append({
                    "name": nm,
                    "description": f"Each {ent} belongs to exactly one {dim_ent} "
                                   f"(many {ent} records per {dim_ent}). Join: {cols}.",
                    "roles": [{"concept": dim_ent}],
                    "verbalizes": [v1, v2],
                    "multiplicity": "ManyToOne",
                })
        if identify:
            concept["identify_by"] = [identify]
        concept["relationships"] = relationships
        entities.append(concept)

    # ------------------------------------------------------------------ mappings
    mappings = []
    for name in order:
        ds, ent = datasets[name], entity_name(name)
        pk = key_of(name, ds)
        if pk is None:
            continue
        pk_rel = field_names(name, pk)[1]
        ident = {"relationship": pk_rel, "expression": f"{name}.{pk}"}
        children = []
        for f in ds["fields"]:
            if f["name"] == pk or (name, f["name"]) in fk_columns:
                continue
            vt, rel_name = field_names(name, f["name"])
            children.append({"object_mapping": {"concept": vt, "expression": f"{name}.{column(f)}"},
                             "relationship": rel_name})
        if rels:
            for r in rels:
                if r["from"] != name:
                    continue
                dim = r["to"]
                dim_pk = (datasets[dim].get("primary_key") or [r["to_columns"][0]])[0]
                children.append({
                    "object_mapping": {"concept": entity_name(dim), "referent_mappings": [
                        {"relationship": field_names(dim, dim_pk)[1],
                         "expression": f"{name}.{r['from_columns'][0]}"}]},
                    "relationship": LINKS.get((name.lower(), dim.lower()),
                                             (f"has_{snake(entity_name(dim))}",))[0]})
        mappings.append({
            "concept": ent,
            "object_mappings": [{"referent_mappings": [ident]}],
            "link_mappings": [{"object_mapping": {"referent_mappings": [ident]},
                               "children": children}],
        })

    logical = {"version": doc.get("version", "0.2.0.dev0"), "name": model["name"],
               "datasets": copy.deepcopy(model["datasets"]), "relationships": copy.deepcopy(rels)}
    for d in logical["datasets"]:
        d.pop("custom_extensions", None)
        for f in d["fields"]:
            f.pop("custom_extensions", None)

    return {
        "version": doc.get("version", "0.2.0.dev0"),
        "name": f"{model['name']}_ontology",
        "description": (f"Business ontology of '{model['name']}': the business vocabulary "
                        f"(concepts) and how the {entity_name(fact)} fact relates to its "
                        f"dimensions ({', '.join(entity_name(d) for d in dims)})."),
        **({"ai_context": model["ai_context"]} if model.get("ai_context") else {}),
        "ontology": list(value_types.values()) + entities,
        "ontology_mappings": [{
            "name": f"{model['name']}_mapping",
            "description": "Binds each ontology concept and relationship to fields of the "
                           "semantic model it was derived from.",
            "semantic_model": logical,
            "concept_mappings": mappings,
        }],
    }


def render(ossie_doc: dict, source_name: str) -> str:
    """The ontology for an Ossie model, as YAML text (pure ASCII, safe for any validator)."""
    out = build(ossie_doc)
    header = ("# yaml-language-server: $schema=../ontology/ontology.json\n"
              f"# Ontology derived from {source_name} (ossie_hub.ontology)\n"
              "# Business names live in the tables at the top of ossie_hub/ontology.py.\n")
    return header + yaml.safe_dump(out, sort_keys=False, allow_unicode=False, width=110)


def ontology_path_for(ossie_path: Path) -> Path:
    """output_models/x_ossie.yaml -> output_models/x_ontology.yaml (same folder)."""
    stem = ossie_path.stem
    stem = stem[:-len("_ossie")] if stem.endswith("_ossie") else stem
    return ossie_path.with_name(f"{stem}_ontology.yaml")


def problems(onto: dict) -> list[str]:
    """Anything in the ontology that would silently lose meaning when pushed or used."""
    out = []
    comps = {c["concept"]: c for c in onto.get("ontology", [])}
    mapped = {m["concept"] for om in onto.get("ontology_mappings", [])
              for m in om.get("concept_mappings", [])}
    for name, c in comps.items():
        if c["type"] != "EntityType":
            continue
        if not c.get("identify_by"):
            out.append(f"entity {name} has no identifier")
        if name not in mapped:
            out.append(f"entity {name} is not mapped to any table column")
        for r in c.get("relationships", []):
            for role in r.get("roles", []):
                if role["concept"] not in comps:
                    out.append(f"{name}.{r['name']} refers to unknown concept {role['concept']}")
    return out


def summary(ontology_doc_text: str) -> str:
    d = yaml.safe_load(ontology_doc_text)
    ents = [c["concept"] for c in d["ontology"] if c["type"] == "EntityType"]
    vts = [c for c in d["ontology"] if c["type"] == "ValueType"]
    return f"{len(ents)} entity concepts ({', '.join(ents)}), {len(vts)} value types"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    text = render(yaml.safe_load(src.read_text(encoding="utf-8")), src.name)
    dst.write_text(text, encoding="utf-8")
    print(f"wrote {dst}: {summary(text)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
