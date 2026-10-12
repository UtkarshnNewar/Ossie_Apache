import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "output_models" / "oadm_r3_ossie.yaml"
OUTPUT_PATH = ROOT / "output_models" / "oadm_r3_universal_enterprise_ontology.yaml"


def safe_concept(value):
    return value if value else "String"


def main():
    doc = yaml.safe_load(MODEL_PATH.read_text(encoding="utf-8"))
    model = doc["semantic_model"][0] if "semantic_model" in doc else doc   # flat or wrapped
    metric_by_table = {}
    for metric in model.get("metrics", []):
        metadata = json.loads(metric["custom_extensions"][0]["data"])
        metric_by_table.setdefault(metadata["rpd_logical_table"], []).append(metric)

    components = []
    entity_names = {dataset["name"] for dataset in model["datasets"]}
    for dataset in model["datasets"]:
        used = set()
        relationships = []
        metric_names = {metric["name"] for metric in metric_by_table.get(dataset["name"], [])}
        for field in dataset.get("fields", []):
            if field["name"] in metric_names:
                continue
            name = field["name"]
            if name in used:
                continue
            used.add(name)
            concept = safe_concept(field.get("datatype"))
            relationships.append({
                "name": name,
                "roles": [{"concept": concept}],
                "multiplicity": "ManyToOne",
                "verbalizes": [f"{{{dataset['name']}}} has {{{concept}}} as {name}"],
            })
        for metric in metric_by_table.get(dataset["name"], []):
            name = metric["name"]
            if name in used:
                continue
            used.add(name)
            expression = metric["expression"]["dialects"][0]["expression"]
            relationships.append({
                "name": name,
                "roles": [{"concept": "Decimal"}],
                "multiplicity": "ManyToOne",
                "derived_by": [expression],
                "verbalizes": [f"{{{dataset['name']}}} has measure {name}"],
            })
        components.append({
            "concept": dataset["name"],
            "type": "EntityType",
            "description": f"Enterprise business concept represented by the OADM_R3 logical table '{dataset['name']}'.",
            "relationships": relationships,
        })

    # a datatype with no portable meaning (Opaque) must still be a declared concept
    declared = {c["concept"] for c in components}
    uses_opaque = any(r["roles"][0]["concept"] == "Opaque"
                      for c in components for r in c.get("relationships", []))
    if uses_opaque and "Opaque" not in declared:
        components.insert(0, {"concept": "Opaque", "type": "ValueType", "extends": ["String"],
                              "description": "A value whose type has no portable equivalent."})

    by_name = {component["concept"]: component for component in components}
    for relation in model.get("relationships", []):
        source = relation["from"]
        target = relation["to"]
        if source not in entity_names or target not in entity_names:
            continue
        component = by_name[source]
        existing = {item["name"] for item in component.get("relationships", [])}
        name = f"related to {target}"
        if name in existing:
            name = f"{name} ({relation['name']})"
        component["relationships"].append({
            "name": name,
            "roles": [{"concept": target}],
            "multiplicity": "ManyToOne",
            "verbalizes": [f"{{{source}}} is related to {{{target}}}"],
        })

    ontology = {
        "version": "0.2.0.dev0",
        "name": "OADM R3 Universal Enterprise Ontology",
        "description": "Universal enterprise ontology derived from the Oracle BI OADM_R3 business model and mapped to the OADM_SAMPLE Autonomous Database schema.",
        "ai_context": {
            "instructions": "Use the RPD presentation names as the canonical business vocabulary. Measures are modeled as quantified relationships on their owning business concept.",
            "synonyms": ["OADM R3 ontology", "OADM enterprise ontology"],
        },
        "ontology": components,
        "prefixes": {"oadm": "https://example.invalid/ontology/oadm-r3#"},
    }
    OUTPUT_PATH.write_text(yaml.safe_dump(ontology, sort_keys=False, allow_unicode=True, width=160), encoding="utf-8")
    print(f"generated {OUTPUT_PATH} with {len(components)} entity concepts")


if __name__ == "__main__":
    main()
