import json
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "import_models" / "oracle" / "bi" / "server" / "base"
R3_BM = "4cfcb3d0-0d34-1000-a0aa-0a0a0a650000"
R3_SCHEMA = "4b8ecbf0-0d34-1000-a0aa-0a0a0a650000"


def local_name(node):
    return node.tag.rsplit("}", 1)[-1]


def children(node, name):
    return [child for child in node if local_name(child) == name]


def first_text(node, name):
    found = next((child for child in node.iter() if local_name(child) == name), None)
    return (found.text or "").strip() if found is not None else ""


def ext(data):
    return [{"vendor_name": "ORACLE_BI", "data": json.dumps(data, ensure_ascii=False)}]


def datatype(value):
    return {
        "VARCHAR": "String",
        "CHAR": "String",
        "DOUBLE": "Float",
        "INTEGER": "Integer",
        "DECIMAL": "Decimal",
        "DATETIME": "DateTime",
        "TIMESTAMP": "DateTime",
    }.get(value.upper(), "Opaque")


def physical_tables():
    result = {}
    for path in (BASE / "PhysicalTable").glob("*.xml"):
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(path).getroot()
        if R3_SCHEMA not in root.attrib.get("containerRef", ""):
            continue
        result[root.attrib["name"]] = {
            "file": path.name,
            "id": root.attrib.get("mdsid", ""),
            "columns": {c.attrib["name"]: dict(c.attrib) for c in children(root, "PhysicalColumn")},
        }
    result["__by_file__"] = {value["file"]: name for name, value in result.items() if name != "__by_file__"}
    # The RPD defines this as a derived object sourced from DWB_NON_RUTNE.
    if "DWB_NON_RUTNE_MEL" in result:
        result["DWB_NON_RUTNE_MEL"]["source"] = "DWB_NON_RUTNE"
    return result


def parse_logical_tables(phys):
    tables = []
    for path in (BASE / "LogicalTable").glob("*.xml"):
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(path).getroot()
        if R3_BM not in root.attrib.get("subjectAreaRef", ""):
            continue
        fields = []
        measures = []
        source = None
        for column in children(root, "LogicalColumn"):
            attribute = next((n for n in children(column, "AttributeDefn")), None)
            refs = [n.attrib.get("objectRef", "") for n in attribute.iter() if local_name(n) == "RefObject"] if attribute is not None else []
            physical_name = None
            physical_column = None
            if refs:
                match = re.search(r"/PhysicalTable/([^/]+\.xml)#([^\s]+)$", refs[0])
                if match:
                    physical_name = phys["__by_file__"].get(match.group(1))
            desc = first_text(attribute, "ExprTextDesc") if attribute is not None else ""
            if desc:
                parts = [part for part in re.findall(r'"([^"]*)"', desc) if part]
                if parts:
                    physical_column = parts[-1]
                    if physical_name is None and len(parts) >= 2:
                        physical_name = parts[-2]
            if physical_name in phys and source is None:
                source = physical_name
            source_for_expr = phys.get(physical_name, {}).get("source", physical_name)
            if physical_name and physical_column:
                expression = f'"OADM_SAMPLE"."{source_for_expr}"."{physical_column}"'
            else:
                expression = desc or "NULL"
            field = {"name": column.attrib["name"], "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}}
            if physical_name in phys and physical_column in phys[physical_name]["columns"]:
                field["datatype"] = datatype(phys[physical_name]["columns"][physical_column].get("dataType", ""))
            field["custom_extensions"] = ext({"rpd_logical_column": column.attrib.get("mdsid", ""), "physical_table": physical_name, "physical_column": physical_column})
            if any(local_name(n) == "MeasureDefn" for n in column.iter()):
                measure_node = next(n for n in column.iter() if local_name(n) == "MeasureDefn")
                measures.append((column.attrib["name"], expression, column.attrib.get("mdsid", ""), root.attrib["name"], first_text(measure_node, "ExprText")))
            else:
                field["dimension"] = {"is_time": column.attrib["name"].lower() in {"time", "date", "day", "month", "year"}}
            fields.append(field)
        tables.append({"name": root.attrib["name"], "source": source or root.attrib["name"], "fields": fields, "measures": measures, "rpd_id": root.attrib.get("mdsid", "")})
    return tables


def parse_relationships(phys, logical):
    key_map = {}
    table_to_logical = {}
    field_map = {}
    for table in logical:
        source = table["source"]
        table_to_logical.setdefault(source, table["name"])
        for field in table["fields"]:
            data = json.loads(field["custom_extensions"][0]["data"])
            if data.get("physical_column"):
                field_map[(source, data["physical_column"])] = field["name"]
    for path in (BASE / "PhysicalTable").glob("*.xml"):
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(path).getroot()
        if R3_SCHEMA not in root.attrib.get("containerRef", ""):
            continue
        table_name = root.attrib.get("name")
        for key in children(root, "PhysicalKey"):
            columns = []
            for ref in key.iter():
                if local_name(ref) == "RefColumn":
                    ref_id = ref.attrib.get("columnRef", "")
                    match = re.search(r"#([^\s]+)$", ref_id)
                    if match:
                        col_id = match.group(1)
                        for column in children(root, "PhysicalColumn"):
                            if column.attrib.get("mdsid") == col_id:
                                columns.append(column.attrib["name"])
            key_map[key.attrib.get("mdsid", "")] = (table_name, columns)
    relationships = []
    seen = set()
    for path in (BASE / "PhysicalTable").glob("*.xml"):
        root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).parse(path).getroot()
        if R3_SCHEMA not in root.attrib.get("containerRef", ""):
            continue
        local_table = root.attrib.get("name")
        for fk in children(root, "PhysicalForeignKey"):
            target_ref = fk.attrib.get("counterPartKeyRef", "")
            target_id = target_ref.rsplit("#", 1)[-1]
            target = key_map.get(target_id)
            local_cols = []
            for ref in fk.iter():
                if local_name(ref) == "RefColumn":
                    match = re.search(r"#([^\s]+)$", ref.attrib.get("columnRef", ""))
                    if match:
                        for column in children(root, "PhysicalColumn"):
                            if column.attrib.get("mdsid") == match.group(1):
                                local_cols.append(column.attrib["name"])
            if not target or local_table not in table_to_logical or target[0] not in table_to_logical:
                continue
            from_cols = [field_map.get((local_table, c), c) for c in local_cols]
            to_cols = [field_map.get((target[0], c), c) for c in target[1]]
            if len(from_cols) != len(to_cols) or not from_cols:
                continue
            name = fk.attrib.get("name", "relationship")
            unique = (name, table_to_logical[local_table], table_to_logical[target[0]], tuple(from_cols), tuple(to_cols))
            if unique in seen:
                continue
            seen.add(unique)
            relationships.append({"name": name, "from": table_to_logical[local_table], "to": table_to_logical[target[0]], "from_columns": from_cols, "to_columns": to_cols, "custom_extensions": ext({"rpd_physical_foreign_key": fk.attrib.get("mdsid", ""), "physical_from": local_table, "physical_to": target[0]})})
    return relationships


def main():
    phys = physical_tables()
    logical = parse_logical_tables(phys)
    datasets = []
    metric_rows = []
    for table in sorted(logical, key=lambda x: x["name"].lower()):
        fields = table["fields"]
        datasets.append({"name": table["name"], "source": f'OADM_SAMPLE.{phys.get(table["source"], {}).get("source", table["source"])}', "fields": fields, "custom_extensions": ext({"rpd_business_model": "OADM_R3", "rpd_logical_table_id": table["rpd_id"], "rpd_source_table": table["source"]})})
        metric_rows.extend(table["measures"])
    metrics = [{"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expression}]}, "custom_extensions": ext({"rpd_measure": "Measure", "rpd_logical_table": table, "rpd_column_id": cid, "rpd_aggregation_expression": aggregation})} for name, expression, cid, table, aggregation in sorted(metric_rows)]
    relationships = parse_relationships(phys, logical)
    model = {"version": "0.2.0.dev0", "semantic_model": [{"name": "OADM_R3", "description": "Apache Ossie semantic model generated from the Oracle BI OADM_R3 RPD business model and mapped to OADM_SAMPLE in Autonomous Database.", "datasets": datasets, "relationships": relationships, "metrics": metrics, "custom_extensions": ext({"source": "Oracle BI MDS XML export", "rpd_business_model": "OADM_R3", "physical_database": "OADM_SAMPLE_ADB", "note": "Oracle-specific logical joins and presentation metadata are retained in the Oracle extension."})}]}
    output = ROOT / "output_models" / "oadm_r3_ossie.yaml"
    flat = {"version": model["version"], **model["semantic_model"][0]}   # one model per document
    output.write_text(yaml.safe_dump(flat, sort_keys=False, allow_unicode=True, width=160), encoding="utf-8")
    print(f"generated {output} with {len(datasets)} datasets, {len(relationships)} relationships, and {len(metrics)} metrics")


if __name__ == "__main__":
    main()
