import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from ossie_hub import connections
from ossie_hub.ui.server import app

H = {"X-Requested-With": "ossie-ui"}


class UiTests(unittest.TestCase):
    def setUp(self):
        self._old = connections.ROOT
        self._tmp = tempfile.TemporaryDirectory()
        connections.ROOT = Path(self._tmp.name)
        self.c = TestClient(app, base_url="http://127.0.0.1")

    def tearDown(self):
        connections.ROOT = self._old
        self._tmp.cleanup()

    def test_connectors_come_from_specs(self):
        r = self.c.get("/api/connectors").json()
        types = [x["type"] for x in r["connectors"]]
        self.assertIn("fabric", types)
        self.assertIn("databricks", types)
        self.assertIn("snowflake", [x["type"] for x in r["coming_soon"]])
        dbx = next(x for x in r["connectors"] if x["type"] == "databricks")
        self.assertTrue(next(f for f in dbx["fields"] if f["name"] == "token")["secret"])

    def test_mutations_need_the_ui_header(self):
        r = self.c.post("/api/connections", json={"type": "fabric", "name": "x"})
        self.assertEqual(r.status_code, 403)

    def test_foreign_host_is_rejected(self):
        r = TestClient(app, base_url="http://evil.example").get("/api/connections")
        self.assertEqual(r.status_code, 403)

    def test_connect_saves_stores_secret_and_never_returns_it(self):
        with mock.patch("keyring.set_password") as setp, \
             mock.patch("keyring.get_password", return_value="TOKEN-VALUE"), \
             mock.patch("ossie_hub.connectors.databricks.DatabricksConnector.test",
                        return_value={"ok": True, "default_catalog": "workspace"}):
            r = self.c.post("/api/connections", headers=H, json={
                "type": "databricks", "name": "dbx-main",
                "params": {"host": "dbc-x.cloud.databricks.com", "warehouse": "wh"},
                "secrets": {"token": "TOKEN-VALUE"}})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["test"]["ok"])
            setp.assert_called_once_with("ossie_hub", "databricks/dbx-main/token", "TOKEN-VALUE")
            listing = self.c.get("/api/connections").json()
            self.assertNotIn("TOKEN-VALUE", str(listing))
            self.assertNotIn("TOKEN-VALUE", r.text)
            self.assertEqual(listing["connections"][0]["last_test"]["ok"], True)
        disk = (connections.ROOT / "databricks/connections/dbx-main.json").read_text()
        self.assertNotIn("TOKEN-VALUE", disk)

    def test_missing_required_secret_is_rejected(self):
        r = self.c.post("/api/connections", headers=H, json={
            "type": "databricks", "name": "d", "params": {"host": "h", "warehouse": "w"}})
        self.assertEqual(r.status_code, 400)

    def test_failed_connection_is_saved_with_the_error(self):
        from ossie_hub.connectors.base import ConnectorError
        with mock.patch("keyring.set_password"), mock.patch("keyring.get_password", return_value="t"), \
             mock.patch("ossie_hub.connectors.databricks.DatabricksConnector.test",
                        side_effect=ConnectorError("bad token")):
            r = self.c.post("/api/connections", headers=H, json={
                "type": "databricks", "name": "d", "params": {"host": "h", "warehouse": "w"},
                "secrets": {"token": "t"}}).json()
        self.assertFalse(r["test"]["ok"])
        self.assertIn("bad token", r["test"]["error"])


if __name__ == "__main__":
    unittest.main()


class TransferUiTests(unittest.TestCase):
    def setUp(self):
        self._old = connections.ROOT
        self._tmp = tempfile.TemporaryDirectory()
        connections.ROOT = Path(self._tmp.name)
        connections.save("fab", "fabric", {"auth_mode": "interactive_browser"})
        connections.save("dbx", "databricks", {"host": "h", "token": "env:T", "warehouse": "w"})
        self.c = TestClient(app, base_url="http://127.0.0.1")
        from ossie_hub.ui import server
        server.JOBS.clear()

    def tearDown(self):
        connections.ROOT = self._old
        self._tmp.cleanup()

    def test_dropdown_endpoints_use_the_right_connection_type(self):
        fake = mock.Mock()
        fake.list_workspaces.return_value = [{"id": "w1", "name": "WS"}]
        with mock.patch("ossie_hub.ui.server.connections.connect", return_value=fake):
            r = self.c.get("/api/fabric/fab/workspaces").json()
            self.assertEqual(r["items"][0]["name"], "WS")
            # a Databricks connection cannot be used where Fabric is expected
            bad = self.c.get("/api/fabric/dbx/workspaces")
            self.assertEqual(bad.status_code, 400)

    def test_run_requires_confirmation(self):
        body = {"source": "fab", "target": "dbx", "workspace": "w", "model": "m",
                "catalog": "c", "target_schema": "s"}
        r = self.c.post("/api/transfer/run", headers=H, json=body)
        self.assertEqual(r.status_code, 400)
        self.assertIn("confirmation", r.text)

    def test_run_executes_as_a_job_and_reports_verification(self):
        import time
        from ossie_hub.ui import server
        prep = mock.Mock(catalog="c", schema="s", doc={}, plan=mock.Mock(), source=mock.Mock())
        res = {"tables": {"orders": {"expected_rows": 1, "loaded_rows": 1}}, "rows": {}}
        check = {"total_sales": {"expected": 10.0, "got": 10.0, "ok": True}}
        with mock.patch("ossie_hub.ui.server.connections.connect"), \
             mock.patch.object(server.transfer_mod, "prepare_fabric_to_databricks", return_value=prep), \
             mock.patch.object(server.executor, "run_plan", return_value=res) as run_plan, \
             mock.patch.object(server.transfer_mod, "verify", return_value=check):
            r = self.c.post("/api/transfer/run", headers=H, json={
                "source": "fab", "target": "dbx", "workspace": "w", "model": "m",
                "catalog": "c", "target_schema": "s", "confirm": True})
            self.assertEqual(r.status_code, 200, r.text)
            job = r.json()["job_id"]
            for _ in range(50):
                j = self.c.get(f"/api/jobs/{job}").json()
                if j["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(j["status"], "done")
        self.assertTrue(j["result"]["all_ok"])
        self.assertEqual(j["result"]["target"], "c.s")
        self.assertTrue(run_plan.call_args.kwargs["execute"])
        self.assertFalse(run_plan.call_args.kwargs["overwrite"])  # off unless explicitly ticked

    def test_failed_job_surfaces_the_error(self):
        import time
        from ossie_hub.ui import server
        with mock.patch("ossie_hub.ui.server.connections.connect"), \
             mock.patch.object(server.transfer_mod, "prepare_fabric_to_databricks",
                               side_effect=RuntimeError("boom")):
            job = self.c.post("/api/transfer/run", headers=H, json={
                "source": "fab", "target": "dbx", "workspace": "w", "model": "m",
                "catalog": "c", "target_schema": "s", "confirm": True}).json()["job_id"]
            for _ in range(50):
                j = self.c.get(f"/api/jobs/{job}").json()
                if j["status"] != "running":
                    break
                time.sleep(0.05)
        self.assertEqual(j["status"], "failed")
        self.assertIn("boom", j["error"])
