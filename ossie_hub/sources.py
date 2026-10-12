"""Row sources for a migration: where the data rows are read from.

Both sources return rows as {column: str | None}, so the executor does not care whether the
rows came from a file or a lakehouse.
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from ossie_hub.connectors.base import ConnectorError


class RowSource(Protocol):
    label: str

    def rows(self, table: str) -> list[dict]: ...


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(v)
    return str(v)


class CsvSource:
    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.label = f"CSV files in {self.dir}"
        self._files = {p.stem.lower(): p for p in self.dir.glob("*.csv")}

    def rows(self, table: str) -> list[dict]:
        path = self._files.get(table.lower())
        if path is None:
            raise ConnectorError(f"no CSV for table {table} in {self.dir}")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            return [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(fh)]


class LakehouseSource:
    """Reads Delta tables straight from OneLake (Fabric lakehouse) with the signed-in identity."""

    def __init__(self, fabric, workspace: str, lakehouse: str, schema: str | None = None):
        self.fabric = fabric
        self.ws = fabric._workspace_id(workspace)
        items = fabric._paged(f"/workspaces/{self.ws}/items")
        hits = [i for i in items if i["type"] == "Lakehouse"
                and (i["displayName"].lower() == lakehouse.lower() or i["id"] == lakehouse)]
        if len(hits) != 1:
            raise ConnectorError(f"lakehouse '{lakehouse}' matched {len(hits)} items")
        self.lh = hits[0]["id"]
        self.schema = schema
        self.label = f"Fabric lakehouse {workspace}/{lakehouse}" + (f" schema {schema}" if schema else "")

    def rows(self, table: str) -> list[dict]:
        from deltalake import DeltaTable
        token = self.fabric._credential().get_token("https://storage.azure.com/.default").token
        sub = f"{self.schema}/{table}" if self.schema else table
        uri = f"abfss://{self.ws}@onelake.dfs.fabric.microsoft.com/{self.lh}/Tables/{sub}"
        try:
            dt = DeltaTable(uri, storage_options={"bearer_token": token,
                                                  "use_fabric_endpoint": "true"})
            data = dt.to_pyarrow_table().to_pylist()
        except Exception as e:  # surface a clear, connector-level error
            raise ConnectorError(f"could not read lakehouse table {sub}: {e}") from e
        return [{k: _text(v) for k, v in row.items()} for row in data]


class CachedSource:
    """Wraps a source so each table is read once (lets us inspect rows, then reuse them)."""

    def __init__(self, inner):
        self.inner, self.label, self._cache = inner, inner.label, {}

    def rows(self, table: str) -> list[dict]:
        if table not in self._cache:
            self._cache[table] = self.inner.rows(table)
        return self._cache[table]


class DatabricksSource:
    """Reads the rows of Unity Catalog tables (SELECT *) through a Databricks connection."""

    def __init__(self, conn, catalog: str, schema: str):
        self.conn, self.catalog, self.schema = conn, catalog, schema
        self.label = f"Databricks {catalog}.{schema}"

    def rows(self, table: str) -> list[dict]:
        out = self.conn.execute(
            f"SELECT * FROM `{self.catalog}`.`{self.schema}`.`{table}`")
        cols = out["columns"]
        return [dict(zip(cols, row)) for row in out["rows"]]
