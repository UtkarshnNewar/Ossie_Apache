"""Command line for Ossie Hub.

    python -m ossie_hub types
    python -m ossie_hub form fabric
    python -m ossie_hub test fabric
    python -m ossie_hub options fabric workspace
    python -m ossie_hub options fabric semantic_model --pick workspace="My WS"
    python -m ossie_hub read-model fabric --pick workspace="My WS" --pick semantic_model=Sales -o out.yaml

Connection fields are passed with --param key=value (secrets as env:VARNAME).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from ossie_hub import connectors, document
from ossie_hub.connectors.base import REGISTRY, ConnectorError


def kv(items: list[str] | None) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"expected key=value, got '{it}'")
        k, v = it.split("=", 1)
        out[k.strip()] = v
    return out


def make(args) -> connectors.Connector:
    if getattr(args, "connection", None):  # a saved connection (with its remembered sign-in)
        from ossie_hub import connections
        return connections.connect(args.connection)
    if args.type not in REGISTRY:
        raise SystemExit(f"unknown connector '{args.type}'. available: {sorted(REGISTRY)}")
    return REGISTRY[args.type](kv(args.param))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ossie_hub", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("types", help="list available connector types")
    for name in ("form", "test", "options", "read-model"):
        p = sub.add_parser(name)
        p.add_argument("type")
        if name in ("test", "options", "read-model"):
            p.add_argument("--param", action="append", help="connection field key=value")
            p.add_argument("--connection", help="use a saved connection (see: connect list)")
        if name in ("options", "read-model"):
            p.add_argument("--pick", action="append", help="picker choice key=value")
        if name == "options":
            p.add_argument("picker")
        if name == "read-model":
            p.add_argument("-o", "--out", type=Path, required=True)
            p.add_argument("--infer-relationships", action="store_true")
            p.add_argument("--ontology-out", type=Path, default=None,
                           help="where to write the ontology (default: next to the model)")
            p.add_argument("--no-ontology", action="store_true", help="skip the ontology")
    pl = sub.add_parser("plan", help="dry-run migration plan for a target platform (no connection)")
    pl.add_argument("--to", required=True, choices=["databricks", "snowflake"])
    pl.add_argument("--from-yaml", type=Path, required=True)
    pl.add_argument("--catalog", help="Databricks catalog / Snowflake database", required=True)
    pl.add_argument("--schema", required=True)
    pl.add_argument("--csv-dir", type=Path, help="source data, used to check key uniqueness")
    pl.add_argument("-o", "--out-dir", type=Path, help="write plan.sql + artifacts here")
    mg = sub.add_parser("migrate", help="create the model + load data on a target (dry run unless --execute)")
    mg.add_argument("--to", required=True, choices=["databricks"])
    mg.add_argument("--from-yaml", type=Path, required=True)
    mg.add_argument("--source", choices=["csv", "fabric"], default="fabric",
                    help="where rows are read from (default: the Fabric lakehouse)")
    mg.add_argument("--csv-dir", type=Path, help="--source csv: folder of <table>.csv files")
    mg.add_argument("--workspace", help="--source fabric: workspace name or id")
    mg.add_argument("--lakehouse", help="--source fabric: lakehouse name or id")
    mg.add_argument("--lake-schema", help="--source fabric: schema folder, e.g. Schema2")
    mg.add_argument("--tenant-id", help="--source fabric: optional tenant id")
    mg.add_argument("--connection-fabric", help="--source fabric: saved Fabric connection to sign in with")
    mg.add_argument("--catalog", required=True)
    mg.add_argument("--schema", required=True)
    mg.add_argument("--connection", help="name of a saved Databricks connection (see: connect list)")
    mg.add_argument("--param", action="append", help="connection field key=value")
    mg.add_argument("--fact", help="fact dataset (defaults to the table that only has outgoing joins)")
    mg.add_argument("--execute", action="store_true", help="actually create/load (default: dry run)")
    mg.add_argument("--overwrite", action="store_true", help="allow reloading tables that hold rows")
    mg.add_argument("--semantic-only", action="store_true",
                    help="only (re)create views and Metric Views; do not touch tables or data")
    ui = sub.add_parser("ui", help="open the connection manager in your browser")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-browser", action="store_true")
    pb = sub.add_parser("pbix", help="export a .pbix's tables to CSV and describe its model as Ossie")
    pb.add_argument("file", type=Path)
    pb.add_argument("--out-dir", type=Path, default=None,
                    help="output folder (default: output_models/<file name>/)")
    pb.add_argument("--connection", default=None,
                    help="saved Fabric connection, used when the report is a live connection to Fabric")
    pb.add_argument("--data-dir", type=Path, default=None,
                    help="where to export CSV data of an embedded report (default: <out-dir>/data)")
    pb.add_argument("--ossie-out", type=Path, default=None,
                    help="path of the Ossie YAML (default: <out-dir>/<file name>_ossie.yaml)")
    pb.add_argument("--name", default=None, help="model name (default: file name)")
    pb.add_argument("--ontology-out", type=Path, default=None,
                    help="where to write the ontology (default: <name>_ontology.yaml next to the model)")
    pb.add_argument("--no-ontology", action="store_true", help="skip the ontology")
    pf = sub.add_parser("publish", help="Databricks tables + Ossie model -> Fabric lakehouse + semantic model")
    pf.add_argument("--from-yaml", type=Path, required=True, help="the Ossie model")
    pf.add_argument("--connection", required=True, help="saved Databricks connection (rows are read from it)")
    pf.add_argument("--catalog", required=True)
    pf.add_argument("--schema", required=True, help="Databricks schema holding the tables")
    pf.add_argument("--workspace", required=True, help="target Fabric workspace")
    pf.add_argument("--lakehouse", required=True, help="target lakehouse (must already exist)")
    pf.add_argument("--lake-schema", required=True, help="schema folder to create in the lakehouse")
    pf.add_argument("--model-name", required=True, help="name of the semantic model to create")
    pf.add_argument("--fabric-connection", default=None, help="saved Fabric connection (default: interactive)")
    pf.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    pf.add_argument("--overwrite", action="store_true", help="allow replacing tables in the schema folder")
    on = sub.add_parser("ontology", help="send an ontology YAML to Databricks (dry run unless --execute)")
    on.add_argument("action", choices=["push"])
    on.add_argument("--from-yaml", type=Path, required=True, help="the ontology YAML")
    on.add_argument("--connection", required=True, help="saved Databricks connection")
    on.add_argument("--catalog", required=True)
    on.add_argument("--schema", required=True, help="schema to hold the ontology (created if missing)")
    on.add_argument("--no-annotate", action="store_true",
                    help="do not add comments/tags to the data tables")
    on.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    on.add_argument("--overwrite", action="store_true",
                    help="allow replacing ontology tables that ossie_hub did not create")
    cn = sub.add_parser("connect", help="manage saved connections (the app asks for the details)")
    cn.add_argument("action", choices=["add", "list", "test", "remove"])
    cn.add_argument("target", nargs="?", help="add: connector type; test/remove: profile name")
    cn.add_argument("--name", help="add: profile name to save as")
    cn.add_argument("--set", action="append", help="add: answer a field up front, key=value")
    cn.add_argument("--advanced", action="store_true",
                    help="add: also ask for rarely needed options (e.g. Tenant ID)")
    cn.add_argument("--remember", action="store_true",
                    help="add: a secret given as env:NAME is read now and kept in the credential store")
    tr = sub.add_parser("transfer", help="move model + data between two saved connections")
    tr.add_argument("--from", dest="src", required=True, help="source profile name")
    tr.add_argument("--to", dest="dst", required=True, help="target profile name")
    tr.add_argument("--workspace")
    tr.add_argument("--model")
    tr.add_argument("--lakehouse")
    tr.add_argument("--lake-schema")
    tr.add_argument("--catalog")
    tr.add_argument("--schema")
    tr.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    tr.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "publish":
            from ossie_hub import connections, transfer
            doc = document.load(args.from_yaml)
            dbx = connections.connect(args.connection)
            fab = (connections.connect(args.fabric_connection) if args.fabric_connection
                   else REGISTRY["fabric"]({"auth_mode": "interactive_browser"}))
            res = transfer.publish_databricks_to_fabric(
                dbx, fab, doc, args.catalog, args.schema, args.workspace, args.lakehouse,
                args.lake_schema, args.model_name, args.execute, args.overwrite)
            if args.execute:
                print(chr(10) + "VERIFICATION (DAX run against the new Fabric model vs. values from the rows):")
                bad = 0
                for name, c in res["verification"].items():
                    flag = "OK " if c["ok"] else "BAD"
                    bad += not c["ok"]
                    print(f"  {flag} {name}: expected {c['expected']:,.4f}  got {c['got']}")
                return 2 if bad else 0
            return 0
        if args.cmd == "pbix":
            import re as _re
            from ossie_hub import outputs, pbix
            info = pbix.inspect_file(args.file)
            stem = args.file.stem
            out_dir = args.out_dir or Path("output_models") / stem
            ossie_out = args.ossie_out or out_dir / f"{stem}_ossie.yaml"
            name = args.name or _re.sub(r"\W+", "_", stem).strip("_").lower()
            print(f"Report      : {args.file.name}  ({info['kind']})")
            if info["kind"] == "embedded":
                data_dir = args.data_dir or out_dir / "data"
                model = pbix.load(args.file)
                exported = pbix.export_csv(model, data_dir)
                doc, notes = pbix.to_ossie(model, name)
                print("Data        : the report carries its own data; tables exported to", data_dir)
                for t, r in exported.items():
                    print(f"  {r['file']:28} {r['rows']:>5} rows, {len(r['columns'])} columns   (from '{t}')")
                origin = args.file.name
            elif info["kind"] == "live":
                if not info.get("model_id"):
                    raise ConnectorError("the live connection in this report has no model id")
                if args.connection:
                    from ossie_hub import connections
                    fab = connections.connect(args.connection)
                else:
                    fab = REGISTRY["fabric"]({"auth_mode": "interactive_browser"})
                ws, mdl = fab.find_model_by_id(info["model_id"])
                print(f"Data source : Fabric. This report is a live connection to the model "
                      f"'{mdl['name']}' in workspace '{ws['name']}'.")
                print("              No data is inside the file, so none is exported; the data stays in Fabric.")
                doc, notes = fab.read_model(workspace=ws["id"], semantic_model=mdl["id"])
                doc["semantic_model"][0]["name"] = name
                doc["semantic_model"][0].setdefault("custom_extensions", []).append({
                    "vendor_name": "POWERBI", "data": json.dumps({"fabric_source": {
                        "workspace": ws["name"], "workspace_id": ws["id"],
                        "model": mdl["name"], "model_id": mdl["id"], "report_file": args.file.name}})})
                origin = f"{args.file.name} -> Fabric model '{mdl['name']}' in '{ws['name']}'"
            else:
                raise ConnectorError(f"{args.file.name}: not a recognised Power BI report "
                                     f"(no data model and no live connection)")
            res = outputs.write_all(
                doc, ossie_out, origin,
                header=f"# Generated by ossie_hub pbix from {origin}\n",
                notes=notes, make_ontology=not args.no_ontology, ontology_out=args.ontology_out)
            outputs.print_summary(res)
            sm = doc["semantic_model"][0]
            print(f"Model       : {len(sm['datasets'])} datasets, "
                  f"{len(sm.get('relationships', []))} relationships")
            for d in sm["datasets"]:
                print(f"   {d['name']:16} <- {d['source']}")
            ok = res["ossie_valid"][0] and res.get("ontology_valid", (True,))[0]
            return 0 if ok else 3
        if args.cmd == "ontology":
            from ossie_hub import connections, ontology_push
            doc = yaml.safe_load(args.from_yaml.read_text(encoding="utf-8"))
            if "ontology" not in doc:
                raise SystemExit(f"{args.from_yaml} is not an ontology file (no 'ontology' section)")
            conn = connections.connect(args.connection)
            print(f"Ontology    : {doc.get('name')}")
            print(f"Target      : {args.catalog}.{args.schema} on Databricks")
            res = ontology_push.run(doc, conn, args.catalog, args.schema, execute=args.execute,
                                    overwrite=args.overwrite, annotate=not args.no_annotate)
            if not args.execute:
                print("To write it, run the same command again with --execute")
            return 0 if not res.get("annotation_failed") else 4
        if args.cmd == "ui":
            import threading
            import webbrowser
            import uvicorn
            from ossie_hub.ui.server import app
            url = f"http://127.0.0.1:{args.port}"
            print(f"Ossie Hub is running at {url}  (Ctrl+C to stop)")
            if not args.no_browser:
                threading.Timer(1.0, lambda: webbrowser.open(url)).start()
            uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
            return 0
        if args.cmd == "connect":
            from ossie_hub import connections
            if args.action == "list":
                shown = False
                for ctype in sorted(REGISTRY):
                    items = connections.listing(ctype)
                    print(f"{REGISTRY[ctype].spec.title} connections ({len(items)})")
                    for it in items:
                        t = it.get("last_test") or {}
                        status = ("never tested" if not t else
                                  f"{'OK' if t.get('ok') else 'FAILED'} @ {t.get('at', '?')}")
                        print(f"  - {it['name']}")
                        print(f"      details : {it['shown']}")
                        print(f"      created : {it.get('created_at')}")
                        print(f"      last use: {it.get('last_used_at') or 'never'}")
                        print(f"      last test: {status}")
                        shown = shown or True
                if not shown:
                    print("none yet. Add one: python -m ossie_hub connect add fabric --name fab")
            elif args.action == "add":
                if args.target not in REGISTRY:
                    raise SystemExit(f"connector type must be one of {sorted(REGISTRY)}")
                spec = REGISTRY[args.target].spec
                print(f"New {spec.title} connection - {spec.description}")
                name = args.name or input("Save as (connection name): ").strip()
                params, secrets = connections.ask(spec, preset=kv(args.set),
                                                  include_advanced=args.advanced)
                for field, value in secrets.items():
                    connections.remember_secret(args.target, name, field, value)
                path = connections.save(name, args.target, params, remember=args.remember)
                print(f"saved '{name}' -> {path}")
                print("secrets are kept in the OS credential store, never in this file")
            elif args.action == "test":
                try:
                    out = connections.connect(args.target).test()
                except ConnectorError as e:
                    connections.record_test(args.target, False, error=str(e))
                    raise
                connections.record_test(args.target, True, summary=out)
                print(json.dumps(out, indent=2))
            elif args.action == "remove":
                print("removed (and its stored secrets)" if connections.remove(args.target)
                      else "no such connection")
        elif args.cmd == "transfer":
            from ossie_hub import connections, transfer
            src, dst = connections.connect(args.src), connections.connect(args.dst)
            kind = (src.spec.type, dst.spec.type)
            if kind == ("fabric", "databricks"):
                return transfer.fabric_to_databricks(src, dst, args, args.execute, args.overwrite)
            if kind == ("databricks", "fabric"):
                return transfer.databricks_to_fabric(src, dst, args, args.execute)
            raise SystemExit(f"transfer {kind[0]} -> {kind[1]} is not supported yet")
        elif args.cmd == "types":
            for t, cls in sorted(REGISTRY.items()):
                print(f"{t:12} {cls.spec.title} - {cls.spec.description}")
        elif args.cmd == "migrate":
            from ossie_hub import executor
            from ossie_hub.connectors.databricks import DatabricksConnector
            from ossie_hub.profile import nonunique_keys
            from ossie_hub.targets import plan_databricks
            from ossie_hub import sources
            doc = document.load(args.from_yaml)
            if args.connection:
                from ossie_hub import connections
                conn = connections.connect(args.connection)
            else:
                conn = DatabricksConnector(kv(args.param))
            if args.source == "fabric":
                # Anything not given is read from the Ossie file itself: the importer records the
                # Fabric workspace, and each dataset's source is `lakehouse.schema.table`.
                model0 = doc["semantic_model"][0]
                recorded = {}
                for e in model0.get("custom_extensions") or []:
                    try:
                        recorded.update(json.loads(e.get("data") or "{}").get("fabric_source", {}))
                    except json.JSONDecodeError:
                        pass
                parts = str(model0["datasets"][0].get("source", "")).split(".")
                workspace = args.workspace or recorded.get("workspace")
                lakehouse = args.lakehouse or (parts[0] if len(parts) == 3 else None)
                lake_schema = args.lake_schema or (parts[1] if len(parts) == 3 else None)
                if not (workspace and lakehouse):
                    raise SystemExit("cannot tell which Fabric workspace / lakehouse holds the data: "
                                     "pass --workspace and --lakehouse")
                print(f"Fabric data : workspace '{workspace}', lakehouse '{lakehouse}', "
                      f"schema '{lake_schema}'")
                if args.connection_fabric:
                    from ossie_hub import connections
                    fab = connections.connect(args.connection_fabric)
                else:
                    fab = REGISTRY["fabric"]({"auth_mode": "interactive_browser",
                                              **({"tenant_id": args.tenant_id} if args.tenant_id else {})})
                src = sources.LakehouseSource(fab, workspace, lakehouse, lake_schema)
            else:
                if not args.csv_dir:
                    raise SystemExit("--csv-dir is required with --source csv")
                src = sources.CsvSource(args.csv_dir)
            # relationship keys are made unique in transit, so uniqueness assertions are valid
            plan = plan_databricks(doc, args.catalog, args.schema, fact=args.fact)
            print(plan.render()); print()
            res = executor.run_plan(plan, doc, conn, src, args.catalog, args.schema,
                                    execute=args.execute, overwrite=args.overwrite,
                                    semantic_only=args.semantic_only)
            if args.execute:
                view = f"{doc['semantic_model'][0]['name']}_metrics"
                expected = executor.expected_from_rows(doc, res["rows"])
                check = executor.verify_measures(conn, args.catalog, args.schema, view, expected)
                print(chr(10) + "VERIFICATION (Metric View vs. values computed from the source rows):")
                for name, c in check.items():
                    print(f"  {'OK ' if c['ok'] else 'BAD'} {name}: expected {c['expected']:,.2f}"
                          f"  got {c['got']:,.2f}")
                if not all(c["ok"] for c in check.values()):
                    return 2
        elif args.cmd == "plan":
            from ossie_hub.profile import nonunique_keys
            from ossie_hub.targets import PLANNERS
            doc = document.load(args.from_yaml)
            unsafe = nonunique_keys(doc, args.csv_dir) if args.csv_dir else None
            plan = PLANNERS[args.to](doc, args.catalog, args.schema, unsafe_keys=unsafe)
            print(plan.render())
            if args.out_dir:
                args.out_dir.mkdir(parents=True, exist_ok=True)
                nl = chr(10)
                sql = (nl + nl).join(f"-- {s.title}{nl}{s.sql}" for s in plan.steps if s.sql)
                (args.out_dir / "plan.sql").write_text(sql + nl, encoding="utf-8")
                for name, text in plan.artifacts.items():
                    (args.out_dir / name).write_text(text, encoding="utf-8")
                print(f"{nl}wrote {args.out_dir}")
        elif args.cmd == "form":
            print(json.dumps(REGISTRY[args.type].spec.form(), indent=2))
        elif args.cmd == "test":
            print(json.dumps(make(args).test(), indent=2))
        elif args.cmd == "options":
            for o in make(args).options(args.picker, **kv(args.pick)):
                extra = f"  {o['sql_endpoint']}" if o.get("sql_endpoint") else ""
                print(f"{o['name']:40} {o['id']}{extra}")
        elif args.cmd == "read-model":
            doc, notes = make(args).read_model(
                infer_relationships=args.infer_relationships, **kv(args.pick))
            from ossie_hub import outputs
            res = outputs.write_all(
                doc, args.out, f"Fabric semantic model {kv(args.pick).get('semantic_model', '')}",
                header="# yaml-language-server: $schema=../core-spec/ossie-schema.json\n",
                notes=notes, make_ontology=not args.no_ontology, ontology_out=args.ontology_out)
            outputs.print_summary(res)
            sm = doc["semantic_model"][0]
            print(f"Model       : {len(sm['datasets'])} datasets, "
                  f"{len(sm.get('relationships', []))} relationships")
            for n in notes:
                if not n.startswith("measure "):
                    print("NOTE:", n)
            if not (res["ossie_valid"][0] and res.get("ontology_valid", (True,))[0]):
                return 3
    except ConnectorError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
