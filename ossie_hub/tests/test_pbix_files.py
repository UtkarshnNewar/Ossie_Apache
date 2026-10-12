"""Recognising what kind of Power BI report a .pbix is, and finding its Fabric model."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ossie_hub import pbix
from ossie_hub.connectors.base import ConnectorError
from ossie_hub.connectors.fabric import FabricConnector

MODEL_ID = "954e73b5-d16f-4e00-9662-1548ea8c2d93"


def make_pbix(directory: str, name: str, files: dict) -> Path:
    path = Path(directory) / name
    with zipfile.ZipFile(path, "w") as z:
        for n, content in files.items():
            z.writestr(n, content)
    return path


class InspectFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_with_a_data_model_is_embedded(self):
        p = make_pbix(self.d, "a.pbix", {"DataModel": b"x", "Version": "1.0"})
        self.assertEqual(pbix.inspect_file(p)["kind"], "embedded")

    def test_thin_report_is_a_live_connection_and_exposes_the_model_id(self):
        conn = {"Version": 2, "Connections": [{
            "Name": "EntityDataSource", "ConnectionType": "pbiServiceLive",
            "ConnectionString": f"Data Source=pbiazure://api.powerbi.com;Initial Catalog={MODEL_ID};",
            "PbiModelDatabaseName": MODEL_ID}]}
        p = make_pbix(self.d, "b.pbix", {"Connections": json.dumps(conn)})
        info = pbix.inspect_file(p)
        self.assertEqual((info["kind"], info["model_id"]), ("live", MODEL_ID))

    def test_live_connection_stored_as_utf16_is_read_too(self):
        conn = {"Connections": [{"ConnectionType": "pbiServiceLive",
                                 "PbiModelDatabaseName": MODEL_ID}]}
        p = make_pbix(self.d, "c.pbix", {"Connections": json.dumps(conn).encode("utf-16")})
        self.assertEqual(pbix.inspect_file(p)["model_id"], MODEL_ID)

    def test_unrecognised_file_is_reported_as_unknown(self):
        p = make_pbix(self.d, "d.pbix", {"Version": "1.0"})
        self.assertEqual(pbix.inspect_file(p)["kind"], "unknown")


class FindModelTests(unittest.TestCase):
    def make(self):
        c = FabricConnector({"auth_mode": "interactive_browser"}, token="T")
        c.list_workspaces = lambda: [{"id": "w1", "name": "Closed"}, {"id": "w2", "name": "Open"}]

        def models(ws):
            if ws == "w1":
                raise ConnectorError("403")           # a workspace we cannot list is skipped
            return [{"id": MODEL_ID.upper(), "name": "Sales"}]
        c.list_models = models
        return c

    def test_finds_the_workspace_that_holds_the_model_ignoring_case(self):
        ws, m = self.make().find_model_by_id(MODEL_ID)
        self.assertEqual((ws["name"], m["name"]), ("Open", "Sales"))

    def test_unknown_model_gives_a_clear_error(self):
        with self.assertRaises(ConnectorError):
            self.make().find_model_by_id("00000000-0000-0000-0000-000000000000")


if __name__ == "__main__":
    unittest.main()
