import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ossie_hub import connections
from ossie_hub import connectors  # noqa: F401  (registers connectors)
from ossie_hub.connectors.base import REGISTRY, ConnectorError, resolve_secret


class ConnectionStoreTests(unittest.TestCase):
    def setUp(self):
        self._old = connections.ROOT
        self._tmp = tempfile.TemporaryDirectory()
        connections.ROOT = Path(self._tmp.name)

    def tearDown(self):
        connections.ROOT = self._old
        self._tmp.cleanup()

    def test_layout_is_one_list_per_connector_type(self):
        connections.save("dbx", "databricks", {"host": "h", "token": "env:T", "warehouse": "w"})
        connections.save("fab", "fabric", {"auth_mode": "interactive_browser"})
        self.assertTrue((connections.ROOT / "databricks" / "connections" / "dbx.json").exists())
        self.assertTrue((connections.ROOT / "fabric" / "connections" / "fab.json").exists())
        self.assertEqual([r["name"] for r in connections.listing("databricks")], ["dbx"])
        self.assertEqual(len(connections.listing()), 2)

    def test_save_refuses_plaintext_secret(self):
        with self.assertRaises(ConnectorError):
            connections.save("x", "databricks", {"host": "h", "token": "dapi_plain", "warehouse": "w"})

    def test_file_keeps_details_and_history_but_no_secret(self):
        connections.save("dbx", "databricks", {"host": "h", "token": "keyring:", "warehouse": "w"})
        data = json.loads((connections.ROOT / "databricks/connections/dbx.json").read_text())
        for key in ("created_at", "updated_at", "last_used_at", "last_test"):
            self.assertIn(key, data)
        self.assertEqual(data["params"]["token"], "keyring:")
        self.assertEqual(connections.listing()[0]["shown"]["token"], "<secret>")

    def test_resave_preserves_created_at_and_history(self):
        connections.save("fab", "fabric", {"auth_mode": "interactive_browser"})
        connections.record_test("fab", True, summary={"workspaces_visible": 12})
        first = connections.listing("fabric")[0]
        connections.save("fab", "fabric", {"auth_mode": "device_code"})
        second = connections.listing("fabric")[0]
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertTrue(second["last_test"]["ok"])

    def test_connect_resolves_keyring_secret_and_stamps_last_used(self):
        connections.save("dbx", "databricks", {"host": "h", "token": "keyring:", "warehouse": "w"})
        with mock.patch("keyring.get_password", return_value="SECRET") as gp:
            conn = connections.connect("dbx")
        gp.assert_called_once_with("ossie_hub", "databricks/dbx/token")
        self.assertEqual(conn._token, "SECRET")
        self.assertIsNotNone(connections.listing("databricks")[0]["last_used_at"])

    def test_missing_secret_is_a_clear_error(self):
        connections.save("dbx", "databricks", {"host": "h", "token": "keyring:", "warehouse": "w"})
        with mock.patch("keyring.get_password", return_value=None):
            with self.assertRaises(ConnectorError):
                connections.connect("dbx")

    def test_remove_forgets_secret(self):
        connections.save("dbx", "databricks", {"host": "h", "token": "keyring:", "warehouse": "w"})
        with mock.patch("keyring.delete_password") as dp:
            self.assertTrue(connections.remove("dbx"))
        dp.assert_called_with("ossie_hub", "databricks/dbx/token")

    def test_invalid_names_and_missing(self):
        with self.assertRaises(ConnectorError):
            connections.save("../evil", "fabric", {"auth_mode": "interactive_browser"})
        with self.assertRaises(ConnectorError):
            connections.find("nope")

    def test_ask_skips_hidden_fields(self):
        spec = REGISTRY["fabric"].spec
        params, secrets = connections.ask(spec, ask_fn=lambda _: "",
                                          preset={"auth_mode": "interactive_browser"})
        self.assertNotIn("client_id", params)
        self.assertEqual(secrets, {})

    def test_ask_typed_secret_goes_to_secrets_not_params(self):
        spec = REGISTRY["databricks"].spec
        answers = {"Workspace host": "dbc-x.cloud.databricks.com", "SQL warehouse": "wh"}

        def fake(prompt):
            return next((v for k, v in answers.items() if prompt.startswith(k)), "")
        params, secrets = connections.ask(spec, ask_fn=fake, secret_fn=lambda _: "tok123")
        self.assertEqual(params["token"], "keyring:")
        self.assertEqual(secrets, {"token": "tok123"})

    def test_tenant_id_must_be_a_code_or_domain_not_an_email(self):
        spec = REGISTRY["fabric"].spec
        base = {"auth_mode": "interactive_browser"}
        self.assertEqual(spec.validate(dict(base)), [])                                  # empty is fine
        self.assertEqual(spec.validate({**base, "tenant_id": "92186c34-11e6-4cc1-bf9e-bfabacd116b4"}), [])
        self.assertEqual(spec.validate({**base, "tenant_id": "contoso.onmicrosoft.com"}), [])
        errs = spec.validate({**base, "tenant_id": "someone@contoso.onmicrosoft.com"})
        self.assertTrue(errs and "email" in errs[0])

    def test_saving_a_fabric_connection_with_an_email_tenant_is_not_possible_through_ask(self):
        spec = REGISTRY["fabric"].spec
        with self.assertRaises(ConnectorError):
            connections.ask(spec, ask_fn=lambda _: "someone@contoso.onmicrosoft.com",
                            preset={"auth_mode": "interactive_browser"}, include_advanced=True)

    def test_normal_prompt_asks_for_email_not_tenant(self):
        spec = REGISTRY["fabric"].spec
        asked = []

        def fake(prompt):
            asked.append(prompt)
            return "me@contoso.com" if "email" in prompt.lower() else ""
        params, _ = connections.ask(spec, ask_fn=fake, preset={"auth_mode": "interactive_browser"})
        self.assertEqual(params["login_email"], "me@contoso.com")
        self.assertNotIn("tenant_id", params)
        self.assertFalse(any("Tenant ID" in a for a in asked))          # tenant is advanced
        self.assertTrue(any("email" in a.lower() for a in asked))

    def test_advanced_option_asks_for_tenant_too(self):
        spec = REGISTRY["fabric"].spec
        asked = []
        connections.ask(spec, ask_fn=lambda p: asked.append(p) or "",
                        preset={"auth_mode": "interactive_browser"}, include_advanced=True)
        self.assertTrue(any("Tenant ID" in a for a in asked))

    def test_email_must_look_like_an_email(self):
        spec = REGISTRY["fabric"].spec
        self.assertEqual(spec.validate({"auth_mode": "interactive_browser", "login_email": "me@contoso.com"}), [])
        self.assertTrue(spec.validate({"auth_mode": "interactive_browser", "login_email": "not an email"}))

    def test_sign_in_uses_the_email_as_a_hint_and_remembers_per_account(self):
        from unittest import mock
        from ossie_hub.connectors.fabric import FabricConnector
        from ossie_hub.connectors.fabric.connector import record_path
        self.assertNotEqual(record_path("a@x.com"), record_path("b@x.com"))
        self.assertEqual(record_path("A@X.com"), record_path("a@x.com"))
        with mock.patch("azure.identity.InteractiveBrowserCredential") as cred,              mock.patch("azure.identity.TokenCachePersistenceOptions"):
            FabricConnector({"auth_mode": "interactive_browser",
                             "login_email": "me@contoso.com"})._credential()
        self.assertEqual(cred.call_args.kwargs["login_hint"], "me@contoso.com")

    def test_resolve_secret_env(self):
        os.environ["OSSIE_TEST_SECRET"] = "s3cret"
        self.assertEqual(resolve_secret("env:OSSIE_TEST_SECRET"), "s3cret")
        with self.assertRaises(ValueError):
            resolve_secret("env:OSSIE_DOES_NOT_EXIST")


if __name__ == "__main__":
    unittest.main()
