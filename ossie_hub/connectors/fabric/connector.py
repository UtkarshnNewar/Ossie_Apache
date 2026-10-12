"""Microsoft Fabric connector: lists workspaces / semantic models and reads a model as Ossie.

Uses the Fabric REST API (https://api.fabric.microsoft.com/v1). Read-only: the only call that
is not a GET is `getDefinition`, which exports a model definition and changes nothing.
"""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any

import requests

from ossie_hub.connectors.base import Connector, ConnectorError, register, resolve_secret
from ossie_hub.spec import ConnectorSpec, Field, Picker
from ossie_hub.tmdl import build, parse_tmdl

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"
RECORD = Path(".ossie_hub") / "fabric_auth_record.json"   # used when no email is given


def record_path(email: str = "") -> Path:
    """Where the remembered (non-secret) sign-in record lives; one per account."""
    if not email:
        return RECORD
    import hashlib
    key = hashlib.sha1(email.lower().encode("utf-8")).hexdigest()[:10]
    return Path(".ossie_hub") / f"fabric_auth_{key}.json"
GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


@register
class FabricConnector(Connector):
    spec = ConnectorSpec(
        type="fabric",
        title="Microsoft Fabric",
        description="Read semantic models (and their lakehouse endpoints) from a Fabric workspace.",
        capabilities=["test", "list_workspaces", "list_models", "list_lakehouses", "read_model"],
        fields=[
            Field("auth_mode", "Sign-in method", "select", required=True,
                  default="interactive_browser",
                  options=["interactive_browser", "device_code", "service_principal"],
                  help="interactive_browser opens a Microsoft sign-in window (recommended)."),
            Field("login_email", "Your Microsoft account email",
                  help="Optional. The work or school email you sign in to Fabric with. It "
                       "pre-selects that account in the sign-in window. It is not a password.",
                  visible_when={"auth_mode": ["interactive_browser"]},
                  pattern=r"[^@\s]+@[^@\s]+\.[^@\s]+",
                  pattern_hint="must look like name@company.com"),
            Field("tenant_id", "Tenant ID", advanced=True,
                  help="Optional: leave EMPTY for browser sign-in. Required only for a service "
                       "principal. It is a code like 92186c34-11e6-4cc1-bf9e-bfabacd116b4 or a "
                       "domain like contoso.onmicrosoft.com. It is NOT your email address.",
                  pattern=r"([0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}|[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)",
                  pattern_hint="must be a tenant ID code or a domain such as contoso.onmicrosoft.com, "
                               "not an email address. Leave it empty for browser sign-in."),
            Field("client_id", "Client ID", required=True,
                  visible_when={"auth_mode": ["service_principal"]}),
            Field("client_secret", "Client secret", required=True, secret=True,
                  visible_when={"auth_mode": ["service_principal"]},
                  help="Pass as env:VARIABLE_NAME so the secret is not typed or logged."),
        ],
        pickers=[
            Picker("workspace", "Workspace"),
            Picker("semantic_model", "Semantic model", depends_on=["workspace"]),
            Picker("lakehouse", "Lakehouse", depends_on=["workspace"], required=False),
            Picker("lakehouse_table", "Lakehouse table", depends_on=["workspace", "lakehouse"],
                   required=False),
        ],
    )

    def __init__(self, params: dict[str, Any], session: requests.Session | None = None,
                 token: str | None = None):
        super().__init__(params)
        self._http = session or requests.Session()
        self._token = token  # tests inject a token; real runs authenticate lazily

    # ------------------------------------------------------------------ auth + http
    def _credential(self):
        """Build the Azure credential. Browser/device flows persist their token cache and a
        non-secret 'authentication record' under .ossie_hub/, so later runs sign in silently."""
        from azure import identity
        p = self.params
        mode = p["auth_mode"]
        tenant = p.get("tenant_id") or None
        if mode == "service_principal":
            return identity.ClientSecretCredential(
                tenant, p["client_id"], resolve_secret(p["client_secret"]))
        kwargs: dict[str, Any] = {"tenant_id": tenant}
        email = (p.get("login_email") or "").strip()
        self._record_file = record_path(email)
        self._have_record = False
        try:
            kwargs["cache_persistence_options"] = identity.TokenCachePersistenceOptions(
                name="ossie_hub_fabric")
            if self._record_file.exists():
                rec = identity.AuthenticationRecord.deserialize(
                    self._record_file.read_text(encoding="utf-8"))
                # a remembered sign-in is only reused for the same account
                if not email or rec.username.lower() == email.lower():
                    kwargs["authentication_record"] = rec
                    self._have_record = True
        except Exception:  # persistence is a convenience; never block sign-in on it
            kwargs.pop("cache_persistence_options", None)
            kwargs.pop("authentication_record", None)
        if mode == "device_code":
            return identity.DeviceCodeCredential(**kwargs)
        if email:
            kwargs["login_hint"] = email   # pre-selects this account in the sign-in window
        return identity.InteractiveBrowserCredential(**kwargs)

    def _auth_header(self) -> dict[str, str]:
        if self._token is None:
            cred = self._credential()
            if hasattr(cred, "authenticate") and not self._have_record:
                try:
                    record = cred.authenticate(scopes=[SCOPE])
                    self._record_file.parent.mkdir(parents=True, exist_ok=True)
                    self._record_file.write_text(record.serialize(), encoding="utf-8")
                except Exception:
                    pass  # fall through: get_token below will sign in (without persistence)
            self._token = cred.get_token(SCOPE).token
        return {"Authorization": f"Bearer {self._token}"}

    def _request(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{API}{path}"
        r = self._http.request(method, url, headers=self._auth_header(), timeout=60, **kw)
        if r.status_code >= 400:
            raise ConnectorError(f"Fabric API {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def _paged(self, path: str) -> list[dict]:
        out, url = [], path
        while url:
            body = self._request("GET", url).json()
            out += body.get("value", [])
            url = body.get("continuationUri")
        return out

    # ------------------------------------------------------------------ discovery
    def test(self) -> dict[str, Any]:
        ws = self._paged("/workspaces")
        return {"ok": True, "workspaces_visible": len(ws)}

    def list_workspaces(self) -> list[dict[str, str]]:
        return [{"id": w["id"], "name": w["displayName"]} for w in self._paged("/workspaces")]

    def _workspace_id(self, workspace: str) -> str:
        if GUID.match(workspace):
            return workspace
        hits = [w for w in self.list_workspaces() if w["name"].lower() == workspace.lower()]
        if len(hits) != 1:
            raise ConnectorError(f"workspace '{workspace}' matched {len(hits)} workspaces")
        return hits[0]["id"]

    def list_models(self, workspace: str) -> list[dict[str, str]]:
        ws = self._workspace_id(workspace)
        return [{"id": m["id"], "name": m["displayName"]}
                for m in self._paged(f"/workspaces/{ws}/semanticModels")]

    def find_model_by_id(self, model_id: str) -> tuple[dict[str, str], dict[str, str]]:
        """(workspace, model) for a semantic model id, searching every workspace you can open.
        A thin Power BI report only stores the model id, not where the model lives."""
        for ws in self.list_workspaces():
            try:
                models = self.list_models(ws["id"])
            except ConnectorError:  # a workspace you cannot list (permissions): skip it
                continue
            for m in models:
                if m["id"].lower() == model_id.lower():
                    return ws, m
        raise ConnectorError(f"semantic model {model_id} was not found in any workspace this "
                             f"account can open")

    def list_lakehouses(self, workspace: str) -> list[dict[str, str]]:
        ws = self._workspace_id(workspace)
        out = []
        for lh in self._paged(f"/workspaces/{ws}/lakehouses"):
            sql = (lh.get("properties") or {}).get("sqlEndpointProperties") or {}
            out.append({"id": lh["id"], "name": lh["displayName"],
                        "sql_endpoint": sql.get("connectionString", "")})
        return out

    def list_lakehouse_tables(self, workspace: str, lakehouse: str) -> list[dict[str, str]]:
        """Tables (Delta folders) under <lakehouse>/Tables, as `schema.table`."""
        ws = self._workspace_id(workspace)
        hits = [l for l in self.list_lakehouses(ws)
                if l["name"].lower() == lakehouse.lower() or l["id"] == lakehouse]
        if len(hits) != 1:
            raise ConnectorError(f"lakehouse '{lakehouse}' matched {len(hits)} lakehouses")
        lh = hits[0]["id"]
        token = self._credential().get_token("https://storage.azure.com/.default").token
        headers = {"Authorization": f"Bearer {token}", "x-ms-version": "2021-06-08"}

        def children(path: str) -> list[str]:
            r = requests.get(f"https://onelake.dfs.fabric.microsoft.com/{ws}", headers=headers,
                             timeout=60, params={"resource": "filesystem", "recursive": "false",
                                                 "directory": f"{lh}/{path}"})
            if r.status_code == 404:
                return []
            if not r.ok:
                raise ConnectorError(f"cannot list {path}: {r.status_code} {r.text[:200]}")
            return [p["name"].split("/")[-1] for p in r.json().get("paths", [])
                    if p.get("isDirectory") == "true"]

        out = []
        for top in children("Tables"):
            inner = children(f"Tables/{top}")
            if "_delta_log" in inner:            # `top` is itself a table
                out.append({"id": top, "name": top})
            else:                                # `top` is a schema folder
                out += [{"id": f"{top}.{t}", "name": f"{top}.{t}"} for t in inner
                        if not t.startswith("_")]
        return out

    def options(self, picker: str, **selected: str) -> list[dict[str, str]]:
        if picker == "workspace":
            return self.list_workspaces()
        if picker == "semantic_model":
            return self.list_models(selected["workspace"])
        if picker == "lakehouse":
            return self.list_lakehouses(selected["workspace"])
        if picker == "lakehouse_table":
            return self.list_lakehouse_tables(selected["workspace"], selected["lakehouse"])
        raise ConnectorError(f"unknown picker '{picker}'")

    # ------------------------------------------------------------------ model -> Ossie
    def get_definition_parts(self, workspace: str, model: str) -> list[dict]:
        ws = self._workspace_id(workspace)
        if not GUID.match(model):
            hits = [m for m in self.list_models(ws) if m["name"].lower() == model.lower()]
            if len(hits) != 1:
                raise ConnectorError(f"model '{model}' matched {len(hits)} models")
            model = hits[0]["id"]
        r = self._request("POST", f"/workspaces/{ws}/semanticModels/{model}/getDefinition",
                          params={"format": "TMDL"})
        if r.status_code == 202:  # long-running operation: poll, then fetch the result
            op_url = r.headers["Location"]
            deadline = time.time() + 180
            while True:
                if time.time() > deadline:
                    raise ConnectorError("timed out waiting for getDefinition")
                time.sleep(float(r.headers.get("Retry-After", 2)))
                st = self._request("GET", op_url)
                body = st.json() if st.content else {}
                status = body.get("status")
                if status == "Succeeded":
                    r = self._request("GET", f"{op_url.rstrip('/')}/result")
                    break
                if status in ("Failed", "Cancelled"):
                    raise ConnectorError(f"getDefinition {status}: {body}")
                r = st
        return r.json()["definition"]["parts"]

    @staticmethod
    def parts_to_nodes(parts: list[dict]):
        nodes = []
        for part in parts:
            path = part["path"]
            if not path.endswith(".tmdl") or "/cultures/" in path:
                continue
            text = base64.b64decode(part["payload"]).decode("utf-8-sig")
            nodes += parse_tmdl(text)
        return nodes

    def read_model(self, workspace: str, semantic_model: str,
                   infer_relationships: bool = False, **_: str) -> tuple[dict, list[str]]:
        parts = self.get_definition_parts(workspace, semantic_model)
        name = re.sub(r"\W+", "_", semantic_model).strip("_").lower()
        return build(self.parts_to_nodes(parts), name, infer_relationships)
