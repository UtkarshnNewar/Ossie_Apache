"""Write a model and its data into Microsoft Fabric.

    1. data   : rows -> Delta tables under  <lakehouse>/Tables/<schema>/<table>   (OneLake)
    2. model  : Ossie -> Direct Lake semantic model (TMDL) via the Fabric item API
    3. verify : refresh (framing) then run DAX against the new model and compare

Nothing is written unless `execute=True`. Existing tables / models are never replaced
unless `overwrite=True`; a name clash otherwise stops the run before anything is written.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import requests

from ossie_hub import powerbi_tmdl
from ossie_hub.connectors.base import ConnectorError
from ossie_hub.connectors.fabric import FabricConnector
from ossie_hub.targets.planner import is_physical, physical_column

STORAGE_SCOPE = "https://storage.azure.com/.default"
PBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
PBI = "https://api.powerbi.com/v1.0/myorg"


# --------------------------------------------------------------------------- helpers
def _to_arrow(values: list, datatype: str):
    import pyarrow as pa

    def ts(v):
        if v in (None, ""):
            return None
        dt = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    if datatype == "Integer":
        return pa.array([None if v in (None, "") else int(float(v)) for v in values], pa.int64())
    if datatype in ("Float", "Decimal"):
        return pa.array([None if v in (None, "") else float(v) for v in values], pa.float64())
    if datatype in ("DateTime", "Date"):
        return pa.array([ts(v) for v in values], pa.timestamp("us", tz="UTC"))
    if datatype == "Boolean":
        return pa.array([None if v in (None, "") else str(v).lower() in ("1", "true", "t", "yes")
                         for v in values], pa.bool_())
    return pa.array([None if v is None else str(v) for v in values], pa.string())


class FabricTarget:
    def __init__(self, fab: FabricConnector, workspace: str, lakehouse: str):
        self.fab = fab
        self.ws = fab._workspace_id(workspace)
        self.ws_name = workspace
        items = fab._paged(f"/workspaces/{self.ws}/items")
        hits = [i for i in items if i["type"] == "Lakehouse"
                and (i["displayName"].lower() == lakehouse.lower() or i["id"] == lakehouse)]
        if len(hits) != 1:
            raise ConnectorError(f"lakehouse '{lakehouse}' matched {len(hits)} items")
        self.lh, self.lh_name = hits[0]["id"], hits[0]["displayName"]
        self._items = items

    # ---- read-only preflight
    def existing_model(self, name: str) -> dict | None:
        return next((i for i in self.fab._paged(f"/workspaces/{self.ws}/items")
                     if i["type"] == "SemanticModel" and i["displayName"].lower() == name.lower()), None)

    def _storage_headers(self) -> dict:
        tok = self.fab._credential().get_token(STORAGE_SCOPE).token
        return {"Authorization": f"Bearer {tok}", "x-ms-version": "2021-06-08"}

    def existing_tables(self, schema: str) -> list[str]:
        r = requests.get(f"https://onelake.dfs.fabric.microsoft.com/{self.ws}",
                         headers=self._storage_headers(), timeout=60,
                         params={"resource": "filesystem", "recursive": "false",
                                 "directory": f"{self.lh}/Tables/{schema}"})
        if r.status_code == 404:
            return []
        if not r.ok:
            raise ConnectorError(f"cannot list OneLake Tables/{schema}: {r.status_code} {r.text[:200]}")
        return [p["name"].split("/")[-1] for p in r.json().get("paths", [])
                if p.get("isDirectory") == "true"]

    # ---- writes
    def write_tables(self, schema: str, datasets: list[dict], source, overwrite: bool,
                     log: Callable[[str], None]) -> dict[str, int]:
        import pyarrow as pa
        from deltalake import write_deltalake
        opts = {"bearer_token": self.fab._credential().get_token(STORAGE_SCOPE).token,
                "use_fabric_endpoint": "true"}
        counts = {}
        for d in datasets:
            cols = [(physical_column(f), f.get("datatype", "String"))
                    for f in d["fields"] if is_physical(f)]
            rows = source.rows(d["name"])
            arrays = {c: _to_arrow([r.get(c) for r in rows], dt) for c, dt in cols}
            table = pa.table(arrays)
            uri = (f"abfss://{self.ws}@onelake.dfs.fabric.microsoft.com/{self.lh}"
                   f"/Tables/{schema}/{d['name']}")
            write_deltalake(uri, table, mode="overwrite" if overwrite else "error",
                            storage_options=opts)
            counts[d["name"]] = table.num_rows
            log(f"-> wrote Tables/{schema}/{d['name']}: {table.num_rows} rows")
        return counts

    def _lro(self, r: requests.Response) -> dict:
        if r.status_code != 202:
            return r.json() if r.content else {}
        op = r.headers["Location"]
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(float(r.headers.get("Retry-After", 2)))
            st = self.fab._request("GET", op)
            body = st.json() if st.content else {}
            if body.get("status") == "Succeeded":
                res = self.fab._request("GET", f"{op.rstrip('/')}/result")
                return res.json() if res.content else {}
            if body.get("status") in ("Failed", "Cancelled"):
                raise ConnectorError(f"Fabric operation {body.get('status')}: {body}")
        raise ConnectorError("timed out waiting for Fabric to create the semantic model")

    def create_model(self, display_name: str, parts: list[dict]) -> dict:
        r = self.fab._request("POST", f"/workspaces/{self.ws}/semanticModels",
                              json={"displayName": display_name, "definition": {"parts": parts}})
        return self._lro(r)

    # ---- verify
    def _pbi(self, method: str, path: str, **kw) -> requests.Response:
        tok = self.fab._credential().get_token(PBI_SCOPE).token
        r = requests.request(method, f"{PBI}{path}", timeout=120,
                             headers={"Authorization": f"Bearer {tok}"}, **kw)
        if r.status_code >= 400:
            raise ConnectorError(f"Power BI API {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def refresh_and_wait(self, model_id: str, timeout_s: int = 240) -> str:
        self._pbi("POST", f"/groups/{self.ws}/datasets/{model_id}/refreshes", json={"type": "Full"})
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(3)
            hist = self._pbi("GET", f"/groups/{self.ws}/datasets/{model_id}/refreshes?$top=1").json()
            status = (hist.get("value") or [{}])[0].get("status")
            if status in ("Completed", "Failed", "Disabled"):
                return status
        return "Timeout"

    def dax(self, model_id: str, query: str) -> list[dict]:
        r = self._pbi("POST", f"/groups/{self.ws}/datasets/{model_id}/executeQueries",
                      json={"queries": [{"query": query}], "serializerSettings": {"includeNulls": True}})
        return r.json()["results"][0]["tables"][0].get("rows", [])


def verify_model(target: FabricTarget, model_id: str, expected: dict[str, float],
                 log: Callable[[str], None] = print) -> dict[str, dict]:
    """Run `EVALUATE ROW(...)` per measure against the new model and compare."""
    out = {}
    for name, want in expected.items():
        rows = target.dax(model_id, f"EVALUATE ROW(\"v\", [{name}])")
        got = next(iter(rows[0].values())) if rows else None
        ok = got is not None and abs(float(got) - float(want)) < 0.01
        out[name] = {"expected": want, "got": got, "ok": ok}
    return out
