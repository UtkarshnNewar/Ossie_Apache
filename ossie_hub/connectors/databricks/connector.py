"""Databricks connector (REST only: workspace API + SQL Statement Execution API).

No extra packages: it uses `requests`. All SQL runs on a SQL warehouse chosen by name or id.
Read-only helpers (`test`, `options`) never change anything; `execute` is what the migration
executor uses and is only called after a plan was confirmed.
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

from ossie_hub.connectors.base import Connector, ConnectorError, register, resolve_secret
from ossie_hub.spec import ConnectorSpec, Field, Picker


@register
class DatabricksConnector(Connector):
    spec = ConnectorSpec(
        type="databricks",
        title="Databricks",
        description="Run SQL on a Databricks SQL warehouse; browse Unity Catalog; create tables "
                    "and Metric Views.",
        capabilities=["test", "list_warehouses", "list_catalogs", "list_schemas", "execute"],
        fields=[
            Field("host", "Workspace host", required=True,
                  help="e.g. dbc-xxxx.cloud.databricks.com (no https://)"),
            Field("token", "Personal access token", required=True, secret=True,
                  help="Pass as env:VARIABLE_NAME. Create under User Settings > Developer."),
            Field("warehouse", "SQL warehouse (name or id)", required=True),
        ],
        pickers=[
            Picker("warehouse", "SQL warehouse"),
            Picker("catalog", "Catalog"),
            Picker("schema", "Schema", depends_on=["catalog"]),
        ],
    )

    def __init__(self, params: dict[str, Any], session: requests.Session | None = None):
        super().__init__(params)
        host = re.sub(r"^https?://", "", self.params["host"]).rstrip("/")
        self.base = f"https://{host}"
        self._token = resolve_secret(self.params["token"])
        self._http = session or requests.Session()
        self._warehouse_id: str | None = None

    # ------------------------------------------------------------------ http
    def _call(self, method: str, path: str, **kw) -> dict:
        r = self._http.request(method, f"{self.base}{path}", timeout=90,
                               headers={"Authorization": f"Bearer {self._token}"}, **kw)
        if r.status_code >= 400:
            msg = r.text[:300]
            if r.status_code in (401, 403):
                msg = "authentication/authorization failed (check token and its permissions)"
            raise ConnectorError(f"Databricks API {method} {path} -> {r.status_code}: {msg}")
        return r.json() if r.content else {}

    # ------------------------------------------------------------------ discovery
    def list_warehouses(self) -> list[dict[str, str]]:
        body = self._call("GET", "/api/2.0/sql/warehouses")
        return [{"id": w["id"], "name": w["name"], "state": w.get("state", "")}
                for w in body.get("warehouses", [])]

    def warehouse_id(self) -> str:
        if self._warehouse_id:
            return self._warehouse_id
        want = self.params["warehouse"]
        whs = self.list_warehouses()
        hits = [w for w in whs if w["id"] == want or w["name"].lower() == want.lower()]
        if len(hits) != 1:
            names = ", ".join(w["name"] for w in whs) or "none"
            raise ConnectorError(f"warehouse '{want}' matched {len(hits)} (available: {names})")
        self._warehouse_id = hits[0]["id"]
        return self._warehouse_id

    def options(self, picker: str, **selected: str) -> list[dict[str, str]]:
        if picker == "warehouse":
            return self.list_warehouses()
        if picker == "catalog":
            return [{"id": r[0], "name": r[0]} for r in self.execute("SHOW CATALOGS")["rows"]]
        if picker == "schema":
            cat = selected["catalog"]
            return [{"id": r[0], "name": r[0]}
                    for r in self.execute(f"SHOW SCHEMAS IN `{cat}`")["rows"]]
        raise ConnectorError(f"unknown picker '{picker}'")

    # ------------------------------------------------------------------ SQL
    def execute(self, sql: str, timeout_s: int = 300) -> dict[str, Any]:
        """Run one statement. Returns {"columns": [...], "rows": [[...]]}."""
        body = self._call("POST", "/api/2.0/sql/statements", json={
            "warehouse_id": self.warehouse_id(), "statement": sql,
            "wait_timeout": "30s", "on_wait_timeout": "CONTINUE", "disposition": "INLINE",
            "format": "JSON_ARRAY"})
        deadline = time.time() + timeout_s
        while body.get("status", {}).get("state") in ("PENDING", "RUNNING"):
            if time.time() > deadline:
                raise ConnectorError("timed out waiting for statement")
            time.sleep(1.5)
            body = self._call("GET", f"/api/2.0/sql/statements/{body['statement_id']}")
        st = body.get("status", {})
        if st.get("state") != "SUCCEEDED":
            err = st.get("error", {})
            raise ConnectorError(f"SQL failed ({st.get('state')}): {err.get('message', body)}")
        cols = [c["name"] for c in body.get("manifest", {}).get("schema", {}).get("columns", [])]
        return {"columns": cols, "rows": body.get("result", {}).get("data_array", [])}

    # ------------------------------------------------------------------ interface
    def test(self) -> dict[str, Any]:
        whs = self.list_warehouses()
        who = self.execute("SELECT current_user(), current_catalog()")["rows"][0]
        return {"ok": True, "warehouses_visible": len(whs), "warehouse_id": self.warehouse_id(),
                "user": who[0], "default_catalog": who[1]}

    # ------------------------------------------------------------------ model -> Ossie
    TYPES = {"STRING": "String", "VARCHAR": "String", "CHAR": "String", "BIGINT": "Integer",
             "INT": "Integer", "INTEGER": "Integer", "SMALLINT": "Integer", "TINYINT": "Integer",
             "LONG": "Integer", "DOUBLE": "Float", "FLOAT": "Float", "REAL": "Float",
             "DECIMAL": "Decimal", "NUMERIC": "Decimal", "BOOLEAN": "Boolean", "DATE": "Date",
             "TIMESTAMP": "DateTime", "TIMESTAMP_NTZ": "DateTime"}

    def read_model(self, catalog: str, schema: str, metric_view: str | None = None,
                   **_: str) -> tuple[dict, list[str]]:
        """Read tables (and a Metric View, if present) of a schema as an Ossie model.

        A Metric View carries the joins and measures, so it is imported with the repo's own
        converter; column data types come from information_schema."""
        import sys
        from pathlib import Path
        import yaml
        root = str(Path(__file__).resolve().parents[2] / "vendor")  # see ossie_hub/vendor/README.md
        if root not in sys.path:
            sys.path.append(root)
        from ossie_databricks.metric_view_to_ossie import convert_metric_view_to_ossie

        notes: list[str] = []
        tabs = self.execute(
            f"SELECT table_name, table_type FROM `{catalog}`.information_schema.tables "
            f"WHERE table_schema = '{schema}' ORDER BY table_name")["rows"]
        if not tabs:
            raise ConnectorError(f"no tables found in {catalog}.{schema}")
        cols = self.execute(
            f"SELECT table_name, column_name, data_type FROM `{catalog}`.information_schema.columns "
            f"WHERE table_schema = '{schema}' ORDER BY table_name, ordinal_position")["rows"]
        types: dict[str, dict[str, str]] = {}
        for t, c, dt in cols:
            types.setdefault(t.lower(), {})[c] = self.TYPES.get(str(dt).upper().split("(")[0], "Opaque")

        views = [t for t, kind in tabs if kind == "METRIC_VIEW"]
        if metric_view:
            views = [v for v in views if v.lower() == metric_view.lower()]
            if not views:
                raise ConnectorError(f"metric view '{metric_view}' not found in {catalog}.{schema}")
        if views:
            view = views[0]
            if len(views) > 1:
                notes.append(f"{len(views)} metric views found; using '{view}'")
            ddl = self.execute(f"SHOW CREATE TABLE `{catalog}`.`{schema}`.`{view}`")["rows"][0][0]
            m = re.search(r"\$\$(.*?)\$\$", ddl, re.S)
            if not m:
                raise ConnectorError(f"could not extract YAML from metric view {view}")
            from ossie_hub import document
            doc = document.wrap(yaml.safe_load(convert_metric_view_to_ossie(m.group(1), model_name=view)))
            model = doc["semantic_model"][0]
            # the importer names the fact dataset after the view; name it after its table
            for ds in model.get("datasets", []):
                if ds["name"] == view:
                    new = str(ds["source"]).split(".")[-1]
                    for r in model.get("relationships", []):
                        r["from"] = new if r["from"] == view else r["from"]
                        r["to"] = new if r["to"] == view else r["to"]
                    ds["name"] = new
            for ds in model.get("datasets", []):
                short = str(ds["source"]).split(".")[-1].lower()
                for f in ds.get("fields", []):
                    expr = next((d["expression"] for d in f["expression"]["dialects"]), f["name"])
                    col = str(expr).split(".")[-1]
                    dt = types.get(short, {}).get(col)
                    if dt and "datatype" not in f:
                        f["datatype"] = dt
            return doc, notes

        notes.append("no Metric View in this schema: tables only (no joins or measures)")
        datasets = []
        for t, kind in tabs:
            if kind in ("VIEW", "MATERIALIZED_VIEW"):
                continue
            datasets.append({"name": t, "source": f"{catalog}.{schema}.{t}", "fields": [
                {"name": c, "datatype": dt,
                 "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": c}]}}
                for c, dt in types.get(t.lower(), {}).items()]})
        return {"version": "0.2.0.dev0",
                "semantic_model": [{"name": schema, "datasets": datasets}]}, notes
