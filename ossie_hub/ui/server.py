"""Local web UI for connections: one tab per platform, a form, a Connect button.

Runs on 127.0.0.1 only. The forms are generated from each connector's spec, so a new
connector appears here automatically. Secrets typed in the browser go straight to the OS
credential store and are never returned by any endpoint.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from ossie_hub import connections
from ossie_hub import connectors  # noqa: F401  (registers connectors)
from ossie_hub.connectors.base import REGISTRY, ConnectorError

STATIC = Path(__file__).parent / "static"
# Platforms on the roadmap: shown as disabled tabs until a connector exists.
COMING_SOON = [("snowflake", "Snowflake"), ("dbt", "dbt"), ("tableau", "Tableau"),
               ("powerbi", "Power BI")]

app = FastAPI(title="Ossie Hub", docs_url=None, redoc_url=None)


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Defend a localhost app against other web pages calling it from the user's browser."""
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse({"error": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD") and \
            request.headers.get("x-requested-with") != "ossie-ui":
        return JSONResponse({"error": "missing header"}, status_code=403)
    return await call_next(request)


class NewConnection(BaseModel):
    type: str
    name: str
    params: dict[str, str] = {}
    secrets: dict[str, str] = {}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/connectors")
def list_connectors():
    ready = [REGISTRY[t].spec.form() for t in sorted(REGISTRY)]
    return {"connectors": ready,
            "coming_soon": [{"type": t, "title": n} for t, n in COMING_SOON if t not in REGISTRY]}


@app.get("/api/connections")
def list_connections():
    return {"connections": [
        {"name": r["name"], "type": r["type"], "details": r["shown"],
         "created_at": r.get("created_at"), "last_used_at": r.get("last_used_at"),
         "last_test": r.get("last_test")} for r in connections.listing()]}


def _run_test(name: str) -> dict:
    try:
        summary = connections.connect(name).test()
    except ConnectorError as e:
        connections.record_test(name, False, error=str(e))
        return {"ok": False, "error": str(e)}
    except Exception as e:  # e.g. sign-in window closed, no network
        connections.record_test(name, False, error=f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    connections.record_test(name, True, summary=summary)
    return {"ok": True, "summary": summary}


@app.post("/api/connections")
def add_connection(body: NewConnection):
    if body.type not in REGISTRY:
        raise HTTPException(400, f"unknown connector '{body.type}'")
    spec = REGISTRY[body.type].spec
    secret_fields = {f.name for f in spec.fields if f.secret}
    if set(body.secrets) - secret_fields:
        raise HTTPException(400, "secrets sent for non-secret fields")
    params = {k: v.strip() for k, v in body.params.items() if v.strip()}
    for field in secret_fields:
        if body.secrets.get(field, "").strip():
            params[field] = "keyring:"
        elif spec.is_field_required(field, params):
            raise HTTPException(400, f"'{field}' is required")
    errors = spec.validate(params)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    try:
        for field, value in body.secrets.items():
            if value.strip():
                connections.remember_secret(body.type, body.name, field, value.strip())
        connections.save(body.name, body.type, params)
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e
    return {"saved": True, "test": _run_test(body.name)}


@app.post("/api/connections/{name}/test")
def test_connection(name: str):
    try:
        connections.find(name)
    except ConnectorError as e:
        raise HTTPException(404, str(e)) from e
    return _run_test(name)


@app.delete("/api/connections/{name}")
def delete_connection(name: str):
    return {"removed": connections.remove(name)}


# =========================================================================== transfer
import threading  # noqa: E402
import uuid  # noqa: E402
from argparse import Namespace  # noqa: E402

from ossie_hub import executor  # noqa: E402
from ossie_hub import transfer as transfer_mod  # noqa: E402

JOBS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()


def _typed(name: str, ctype: str):
    """Instantiate a saved connection and make sure it is of the expected type."""
    try:
        kind, _ = connections.find(name)
        if kind != ctype:
            raise HTTPException(400, f"'{name}' is a {kind} connection, expected {ctype}")
        return connections.connect(name)
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e


def _guard(fn):
    try:
        return fn()
    except HTTPException:
        raise
    except ConnectorError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}") from e


@app.get("/api/fabric/{conn}/workspaces")
def fabric_workspaces(conn: str):
    c = _typed(conn, "fabric")
    return {"items": _guard(c.list_workspaces)}


@app.get("/api/fabric/{conn}/workspaces/{workspace}/models")
def fabric_models(conn: str, workspace: str):
    c = _typed(conn, "fabric")
    return {"items": _guard(lambda: c.list_models(workspace))}


@app.get("/api/databricks/{conn}/catalogs")
def databricks_catalogs(conn: str):
    c = _typed(conn, "databricks")
    return {"items": _guard(lambda: c.options("catalog")),
            "default": _guard(c.test).get("default_catalog")}


@app.get("/api/databricks/{conn}/catalogs/{catalog}/schemas")
def databricks_schemas(conn: str, catalog: str):
    c = _typed(conn, "databricks")
    return {"items": _guard(lambda: c.options("schema", catalog=catalog))}


class TransferRequest(BaseModel):
    source: str            # saved Fabric connection name
    target: str            # saved Databricks connection name
    workspace: str
    model: str
    catalog: str
    target_schema: str
    overwrite: bool = False
    confirm: bool = False


def _ns(req: TransferRequest) -> Namespace:
    return Namespace(workspace=req.workspace, model=req.model, catalog=req.catalog,
                     schema=req.target_schema, lakehouse=None, lake_schema=None)


@app.post("/api/transfer/preview")
def transfer_preview(req: TransferRequest):
    """Read-only: reads the model and the source rows, builds the plan, writes nothing."""
    fab, dbx = _typed(req.source, "fabric"), _typed(req.target, "databricks")

    def go():
        prep = transfer_mod.prepare_fabric_to_databricks(fab, dbx, _ns(req), log=lambda _: None)
        res = executor.run_plan(prep.plan, prep.doc, dbx, prep.source, prep.catalog, prep.schema,
                                execute=False, overwrite=True, log=lambda _: None)
        names = {d["name"].lower() for d in prep.doc["semantic_model"][0]["datasets"]}
        conflicts = sorted(t for t, n in res["existing_tables"].items() if t in names and n > 0)
        return {
            "workspace": prep.workspace, "model": prep.model,
            "catalog": prep.catalog, "schema": prep.schema,
            "steps": [{"kind": s.kind, "title": s.title, "sql": s.sql} for s in prep.plan.steps],
            "notes": prep.notes,
            "tables": [{"name": t, **s} for t, s in res["stats"].items()],
            "existing_tables": res["existing_tables"], "conflicts": conflicts}
    return _guard(go)


def _run_job(job_id: str, req: TransferRequest) -> None:
    job = JOBS[job_id]
    log = job["log"].append
    try:
        fab = connections.connect(req.source)
        dbx = connections.connect(req.target)
        prep = transfer_mod.prepare_fabric_to_databricks(fab, dbx, _ns(req), log=log)
        res = executor.run_plan(prep.plan, prep.doc, dbx, prep.source, prep.catalog, prep.schema,
                                execute=True, overwrite=req.overwrite, log=log)
        check = transfer_mod.verify(dbx, prep, res["rows"])
        job["result"] = {"tables": res["tables"], "verification": check,
                         "all_ok": all(c["ok"] for c in check.values()),
                         "target": f"{prep.catalog}.{prep.schema}"}
        job["status"] = "done"
    except Exception as e:  # surfaced to the UI, never crashes the server
        log(f"ERROR: {e}")
        job["status"] = "failed"
        job["error"] = str(e)


@app.post("/api/transfer/run")
def transfer_run(req: TransferRequest):
    if not req.confirm:
        raise HTTPException(400, "confirmation required")
    _typed(req.source, "fabric"), _typed(req.target, "databricks")
    with _JOB_LOCK:
        if any(j["status"] == "running" for j in JOBS.values()):
            raise HTTPException(409, "a transfer is already running")
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "running", "log": [], "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job")
    return {"status": job["status"], "log": job["log"], "result": job["result"],
            "error": job["error"]}
