import json
import re
import uuid
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "output_models" / "oadm_r3_ossie.yaml"
OUT = ROOT / "output_models" / "OADM_R3_PowerBI"
PROJECT = "OADM_R3_PowerBI"
NS = uuid.UUID("a7df8dc7-173e-4ff3-9b7f-0e9f2a0d7f31")


def tag(value):
    return str(uuid.uuid5(NS, value))


def tmdl_name(value):
    value = str(value)
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        return value
    return "'" + value.replace("'", "''") + "'"


def dax_name(value):
    return "'" + str(value).replace("'", "''") + "'"


def dax_column(value):
    return "[" + str(value).replace("]", "]]" ) + "]"


def m_text(value):
    return '"' + str(value).replace('"', '""') + '"'


def extension(field, vendor="ORACLE_BI"):
    for item in field.get("custom_extensions", []):
        if item.get("vendor_name") == vendor:
            try:
                return json.loads(item.get("data", "{}"))
            except json.JSONDecodeError:
                return {}
    return {}


def datatype(value):
    return {
        "String": "string",
        "Integer": "int64",
        "Float": "double",
        "Decimal": "decimal",
        "DateTime": "dateTime",
        "Boolean": "boolean",
    }.get(value, "string")


def source_parts(dataset):
    source = dataset.get("source", "")
    if "." in source:
        schema, table = source.split(".", 1)
    else:
        schema, table = "OADM_SAMPLE", source
    return schema, table


def power_query(dataset, metric_fields=None):
    metric_fields = metric_fields or set()
    schema, physical = source_parts(dataset)
    fields = dataset.get("fields", [])
    missing = []
    for field in fields:
        logical = field["name"]
        target = "__RPD_Value_" + logical if logical in metric_fields else logical
        info = extension(field)
        physical_column = info.get("physical_column")
        if not physical_column:
            missing.append(target)
    lines = [
        "let",
        '    Source = Oracle.Database("TRANSPORTADB_high", [HierarchicalNavigation=true]),',
        f"    Schema = Source{{[Schema={m_text(schema)}]}}[Data],",
        f"    Physical = Schema{{[Name={m_text(physical)}]}}[Data],",
        "    Renamed = Physical,",
    ]
    for index, logical in enumerate(missing):
        step = f"Added{index + 1}"
        lines.append(f"    {step} = Table.AddColumn({('Renamed' if index == 0 else f'Added{index}')}, {m_text(logical)}, each null),")
    final = f"Added{len(missing)}" if missing else "Renamed"
    lines[-1] = lines[-1].rstrip(",")
    lines.append(f"in\n    {final}")
    return "\n".join(lines)


def measure_info(metric, datasets_by_name):
    info = extension(metric)
    owner = info.get("rpd_logical_table")
    table = datasets_by_name.get(owner)
    field_name = metric["name"]
    if table:
        for field in table.get("fields", []):
            if extension(field).get("rpd_logical_column") == info.get("rpd_column_id"):
                field_name = field["name"]
                break
    aggregation = info.get("rpd_aggregation_expression", "SUM(\"%1\")")
    fn = "AVERAGE" if aggregation.strip().upper().startswith("AVG") else "SUM"
    return owner, field_name, fn


def table_file_name(name):
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "Table"
    return safe[:100] + ".tmdl"


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def main():
    model = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))["semantic_model"][0]
    datasets = model["datasets"]
    by_name = {d["name"]: d for d in datasets}
    sm = OUT / f"{PROJECT}.SemanticModel"
    report = OUT / f"{PROJECT}.Report"
    definition = sm / "definition"
    tables_dir = definition / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    report.mkdir(parents=True, exist_ok=True)

    pbip = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
    }
    write_text(OUT / f"{PROJECT}.pbip", json.dumps(pbip, indent=2))
    write_text(OUT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf")

    pbir = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
    }
    write_text(report / "definition.pbir", json.dumps(pbir, indent=2))
    write_text(sm / "definition.pbism", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "5.0",
        "settings": {"qnaEnabled": False},
    }, indent=2))

    model_lines = [
        "model Model",
        "    culture: en-US",
        "    defaultPowerBIDataSourceVersion: powerBI_V3",
        "    sourceQueryCulture: en-US",
        "    valueFilterBehavior: independent",
        "    dataAccessOptions",
        "        legacyRedirects",
        "        returnErrorValuesAsNull",
    ]
    write_text(definition / "model.tmdl", "\n".join(model_lines))
    write_text(definition / "database.tmdl", "database OADM_R3\n    compatibilityLevel: 1606")
    write_text(definition / "dataSources.tmdl", "")

    metrics_by_owner = {}
    metric_fields_by_table = {}
    for metric in model.get("metrics", []):
        owner, field, fn = measure_info(metric, by_name)
        if owner in by_name:
            metrics_by_owner.setdefault(owner, []).append((metric["name"], field, fn))
            metric_fields_by_table.setdefault(owner, set()).add(field)

    for dataset in datasets:
        name = dataset["name"]
        lines = [f"/// Imported from OADM_R3 / {dataset.get('source', '')}", f"table {tmdl_name(name)}", f"    lineageTag: {tag('table:' + name)}", ""]
        for field in dataset.get("fields", []):
            col = field["name"]
            technical_col = "__RPD_Value_" + col if col in metric_fields_by_table.get(name, set()) else col
            lines.extend([
                f"    column {tmdl_name(technical_col)}",
                f"        dataType: {datatype(field.get('datatype'))}",
                f"        lineageTag: {tag('column:' + name + ':' + col)}",
                "        summarizeBy: none",
                f"        sourceColumn: {m_text(extension(field).get('physical_column') or technical_col)}",
                *(["        isHidden"] if technical_col != col else []),
                "",
            ])
        for metric_name, field_name, fn in metrics_by_owner.get(name, []):
            lines.extend([
                f"    measure {tmdl_name(metric_name)} = {fn}({dax_name(name)}{dax_column('__RPD_Value_' + field_name)})",
                "        formatString: #,##0.00",
                "",
            ])
        lines.extend([
            f"    partition {tmdl_name(name + '-Import')} = m",
            "        mode: import",
            "        source = ```",
        ])
        lines.extend("            " + line for line in power_query(dataset, metric_fields_by_table.get(name)).splitlines())
        lines.extend(["            ```", ""])
        write_text(tables_dir / table_file_name(name), "\n".join(lines))

    rel_lines = []
    table_names = set(by_name)
    for index, rel in enumerate(model.get("relationships", []), 1):
        if rel.get("from") not in table_names or rel.get("to") not in table_names:
            continue
        pairs = list(zip(rel.get("from_columns", []), rel.get("to_columns", [])))
        if not pairs:
            continue
        # Power BI Import relationships are single-column relationships. The
        # RPD contains one composite FK; retain its first key pair so the
        # project has one unambiguous active path and remains loadable.
        from_col, to_col = pairs[0]
        rel_lines.extend([
            f"relationship {tag('relationship:' + str(index) + ':' + from_col + ':' + to_col)}",
            f"    fromColumn: {tmdl_name(rel['from'])}.{tmdl_name(from_col)}",
            f"    toColumn: {tmdl_name(rel['to'])}.{tmdl_name(to_col)}",
            "    fromCardinality: many",
            "    toCardinality: many",
            "    crossFilteringBehavior: oneDirection",
            "",
        ])
    write_text(definition / "relationships.tmdl", "\n".join(rel_lines))

    manifest = {
        "project": PROJECT,
        "source": "OADM_SAMPLE_ADB / OADM_SAMPLE",
        "datasets": len(datasets),
        "relationships": len([x for x in model.get("relationships", []) if x.get("from") in table_names and x.get("to") in table_names]),
        "measures": len(model.get("metrics", [])),
        "data_cache": "not generated; Power BI Desktop creates .pbi/cache.abf after refresh",
        "note": "DWB_NON_RUTNE_MEL is represented by the RPD-derived source DWB_NON_RUTNE; the empty compatibility table remains in Oracle.",
    }
    write_text(OUT / "README.md", "# OADM R3 Power BI Project\n\n" + json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
