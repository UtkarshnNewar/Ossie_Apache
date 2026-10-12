"""Offline tests: a fake Fabric API, no network or credentials."""
import base64
import json
import unittest
from pathlib import Path

from ossie_hub.connectors.base import ConnectorError
from ossie_hub.connectors.fabric import FabricConnector

ROOT = Path(__file__).resolve().parents[2]
WS = "5951c1a4-a0ce-40d7-abab-6d35b6c5c76c"
MODEL = "954e73b5-d16f-4e00-9662-1548ea8c2d93"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def parts_from_script(script: str) -> list[dict]:
    """Split the Fabric 'Script TMDL' text (all tables in one file) into per-table parts
    and add a relationships part, mimicking the getDefinition(TMDL) layout."""
    lines = [ln[1:] if ln.startswith("\t") else ln
             for ln in script.replace("\r\n", "\n").split("\n")
             if ln.strip() and ln.strip() != "createOrReplace"]
    chunks, cur = [], []
    for ln in lines:
        if ln.startswith("table ") and cur:
            chunks.append(cur)
            cur = []
        cur.append(ln)
    chunks.append(cur)
    parts = []
    for c in chunks:
        name = c[0].split(" ", 1)[1].strip("'")
        parts.append({"path": f"definition/tables/{name}.tmdl", "payload": b64("\n".join(c)),
                      "payloadType": "InlineBase64"})
    rel = ("relationship r1\n\tfromColumn: orders.Customer_ID\n\ttoColumn: customers.Customer_ID\n\n"
           "relationship r2\n\tfromColumn: orders.Product_ID\n\ttoColumn: products.Product_ID\n\n"
           "relationship r3\n\tisActive: false\n\tfromColumn: orders.Postal_Code\n"
           "\ttoColumn: location.Postal_Code\n")
    parts += [{"path": "definition/relationships.tmdl", "payload": b64(rel),
               "payloadType": "InlineBase64"},
              {"path": "definition/cultures/en-US.tmdl", "payload": b64("cultureInfo en-US"),
               "payloadType": "InlineBase64"},
              {"path": "definition.pbism", "payload": b64("{}"), "payloadType": "InlineBase64"}]
    return parts


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self.text = json.dumps(body)
        self.content = b"x" if body is not None else b""

    def json(self):
        return self._body


class FakeFabric:
    """Minimal fake of the endpoints the connector uses (incl. the 202 long-running flow)."""

    def __init__(self, parts, lro=True):
        self.parts, self.lro, self.calls = parts, lro, []

    def request(self, method, url, headers=None, timeout=None, params=None, **kw):
        assert headers and headers["Authorization"] == "Bearer T"
        self.calls.append((method, url))
        base = "https://api.fabric.microsoft.com/v1"
        if url == f"{base}/workspaces":
            return Resp(body={"value": [{"id": WS, "displayName": "OssieApache_Workspace"}]})
        if url == f"{base}/workspaces/{WS}/semanticModels":
            return Resp(body={"value": [{"id": MODEL, "displayName": "Sales"}]})
        if url == f"{base}/workspaces/{WS}/lakehouses":
            return Resp(body={"value": [{"id": "lh1", "displayName": "Lakehouse_1", "properties": {
                "sqlEndpointProperties": {"connectionString": "x.datawarehouse.fabric.microsoft.com"}}}]})
        if url.endswith("/getDefinition"):
            assert params == {"format": "TMDL"} and method == "POST"
            if self.lro:
                return Resp(202, None, {"Location": f"{base}/operations/op1", "Retry-After": "0"})
            return Resp(body={"definition": {"parts": self.parts}})
        if url == f"{base}/operations/op1":
            return Resp(body={"status": "Succeeded", "id": "op1"})
        if url == f"{base}/operations/op1/result":
            return Resp(body={"definition": {"parts": self.parts}})
        return Resp(404, {"error": url})


def connector(fake):
    return FabricConnector({"auth_mode": "interactive_browser"}, session=fake, token="T")


class FabricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = (ROOT / "Knowledge" / "salesDashboard_datasource.md").read_text(encoding="utf-8")
        cls.parts = parts_from_script(script)

    def test_discovery(self):
        c = connector(FakeFabric(self.parts))
        self.assertEqual(c.options("workspace")[0]["name"], "OssieApache_Workspace")
        self.assertEqual(c.options("semantic_model", workspace="ossieapache_workspace")[0]["id"], MODEL)
        self.assertTrue(c.options("lakehouse", workspace=WS)[0]["sql_endpoint"].endswith("microsoft.com"))

    def test_read_model_long_running(self):
        doc, notes = connector(FakeFabric(self.parts, lro=True)).read_model(
            workspace="OssieApache_Workspace", semantic_model="Sales")
        m = doc["semantic_model"][0]
        self.assertEqual(sorted(d["name"] for d in m["datasets"]),
                         ["customers", "location", "orders", "products"])
        self.assertEqual(len(m["metrics"]), 6)
        # r3 is inactive -> skipped, with a note; r1/r2 come through
        self.assertEqual(sorted(r["to"] for r in m["relationships"]), ["customers", "products"])
        self.assertTrue(any("inactive" in n for n in notes))
        orders = next(d for d in m["datasets"] if d["name"] == "orders")
        self.assertEqual(orders["source"], "Lakehouse_1.Schema2.orders")

    def test_read_model_immediate(self):
        doc, _ = connector(FakeFabric(self.parts, lro=False)).read_model(
            workspace=WS, semantic_model="Sales")
        self.assertEqual(len(doc["semantic_model"][0]["datasets"]), 4)

    def test_unknown_model_is_a_clear_error(self):
        with self.assertRaises(ConnectorError):
            connector(FakeFabric(self.parts)).read_model(workspace=WS, semantic_model="Nope")


class LakehouseTablesTests(unittest.TestCase):
    def test_tables_are_listed_as_schema_dot_table(self):
        from unittest import mock

        def fake_get(url, headers=None, params=None, timeout=None):
            d = params["directory"]
            tree = {
                "lh1/Tables": ["Schema2", "orders_flat"],
                "lh1/Tables/Schema2": ["orders", "products"],
                "lh1/Tables/orders_flat": ["_delta_log", "part-0.parquet"],
            }
            names = tree.get(d, [])
            r = mock.Mock(ok=True, status_code=200)
            r.json.return_value = {"paths": [{"name": f"{d}/{n}", "isDirectory": "true"} for n in names]}
            return r
        c = connector(FakeFabric([]))
        c.list_lakehouses = lambda ws: [{"id": "lh1", "name": "Lakehouse_1", "sql_endpoint": ""}]
        c._credential = lambda: mock.Mock(get_token=lambda scope: mock.Mock(token="T"))
        with mock.patch("requests.get", side_effect=fake_get):
            out = c.list_lakehouse_tables(WS, "Lakehouse_1")
        self.assertEqual(sorted(x["name"] for x in out),
                         ["Schema2.orders", "Schema2.products", "orders_flat"])


class SpecTests(unittest.TestCase):
    def test_visible_when_and_required(self):
        spec = FabricConnector.spec
        self.assertEqual(spec.validate({"auth_mode": "interactive_browser"}), [])
        errs = spec.validate({"auth_mode": "service_principal"})
        self.assertIn("'client_id' is required", errs)
        self.assertIn("'client_secret' is required", errs)
        self.assertTrue(spec.validate({"auth_mode": "bogus"}))
        self.assertTrue(spec.validate({"auth_mode": "interactive_browser", "x": "1"}))

    def test_form_has_no_secret_values_and_schema_marks_write_only(self):
        form = FabricConnector.spec.form()
        self.assertTrue(any(f["secret"] for f in form["fields"]))
        self.assertTrue(FabricConnector.spec.json_schema()["properties"]["client_secret"]["writeOnly"])


if __name__ == "__main__":
    unittest.main()
