"""Saved connections: the app remembers how to reach each platform.

Layout (one list per connector type, so the UI can show "Fabric connections",
"Databricks connections", ...):

    ossie_hub/connectors/<type>/connections/<name>.json

Each file holds the connection's details and history, never a secret:

    {"name": "dbx", "type": "databricks", "params": {...},
     "created_at": "...", "last_used_at": "...",
     "last_test": {"ok": true, "at": "...", "summary": {...}}}

Secrets are referenced, not stored in the file:
    keyring:  value is in the OS credential store (Windows Credential Manager)
    env:NAME  read from an environment variable at run time
    prompt:   asked (hidden input) every time
"""
from __future__ import annotations

import getpass
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ossie_hub.connectors.base import REGISTRY, ConnectorError
from ossie_hub.spec import ConnectorSpec

ROOT = Path(__file__).resolve().parent / "connectors"   # tests may point this elsewhere
NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
KEYRING_SERVICE = "ossie_hub"
SECRET_REFS = ("env:", "keyring:", "prompt:")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def folder(ctype: str) -> Path:
    if ctype not in REGISTRY:
        raise ConnectorError(f"unknown connector type '{ctype}'")
    return ROOT / ctype / "connections"


def _file(ctype: str, name: str) -> Path:
    if not NAME.match(name):
        raise ConnectorError(f"invalid connection name '{name}' (letters, digits, _ . - only)")
    return folder(ctype) / f"{name}.json"


def _secret_names(ctype: str) -> set[str]:
    return {f.name for f in REGISTRY[ctype].spec.fields if f.secret}


def _keyring_user(ctype: str, name: str, field: str) -> str:
    return f"{ctype}/{name}/{field}"


# --------------------------------------------------------------------------- secrets
def remember_secret(ctype: str, name: str, field: str, value: str) -> str:
    """Put a secret in the OS credential store; returns the reference to store in the file."""
    import keyring
    keyring.set_password(KEYRING_SERVICE, _keyring_user(ctype, name, field), value)
    return "keyring:"


def forget_secrets(ctype: str, name: str) -> None:
    import keyring
    for field in _secret_names(ctype):
        try:
            keyring.delete_password(KEYRING_SERVICE, _keyring_user(ctype, name, field))
        except keyring.errors.PasswordDeleteError:
            pass


def _resolve(ctype: str, name: str, params: dict) -> dict:
    """In-memory copy of params with `keyring:` references replaced by their values."""
    out = dict(params)
    for field in _secret_names(ctype):
        if out.get(field) == "keyring:":
            import keyring
            val = keyring.get_password(KEYRING_SERVICE, _keyring_user(ctype, name, field))
            if val is None:
                raise ConnectorError(
                    f"secret '{field}' for connection '{name}' is missing from the credential "
                    f"store; re-add the connection")
            out[field] = val
    return out


# --------------------------------------------------------------------------- CRUD
def save(name: str, ctype: str, params: dict, remember: bool = False) -> Path:
    """Save a connection. Plain-text secrets are refused. With remember=True a secret given
    as env:NAME is read now and stored in the credential store (reference: keyring:)."""
    params = dict(params)
    for field in _secret_names(ctype) & set(params):
        v = str(params[field])
        if v.startswith("env:") and remember:
            import os
            raw = os.environ.get(v[4:])
            if raw is None:
                raise ConnectorError(f"environment variable {v[4:]} is not set")
            params[field] = remember_secret(ctype, name, field, raw)
        elif not v.startswith(SECRET_REFS):
            raise ConnectorError(
                f"refusing to store secret '{field}' as plain text; use env:NAME, prompt:, "
                f"or let the app store it in the credential store")
    path = _file(ctype, name)
    previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "name": name, "type": ctype, "params": params,
        "created_at": previous.get("created_at", _now()), "updated_at": _now(),
        "last_used_at": previous.get("last_used_at"), "last_test": previous.get("last_test"),
    }, indent=2), encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find(name: str) -> tuple[str, Path]:
    hits = [(t, _file(t, name)) for t in REGISTRY if _file(t, name).exists()]
    if not hits:
        raise ConnectorError(f"no saved connection '{name}'. Add one: "
                             f"python -m ossie_hub connect add <type> --name {name}")
    if len(hits) > 1:
        raise ConnectorError(f"'{name}' exists for several types "
                             f"{[t for t, _ in hits]}; names must be unique")
    return hits[0]


def listing(ctype: str | None = None) -> list[dict]:
    """Saved connections (newest-used first), optionally for one connector type."""
    out = []
    for t in ([ctype] if ctype else sorted(REGISTRY)):
        d = folder(t)
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            rec = _read(p)
            secrets = _secret_names(t)
            rec["shown"] = {k: ("<secret>" if k in secrets else v) for k, v in rec["params"].items()}
            out.append(rec)
    return sorted(out, key=lambda r: r.get("last_used_at") or r.get("created_at") or "", reverse=True)


def remove(name: str) -> bool:
    try:
        ctype, path = find(name)
    except ConnectorError:
        return False
    path.unlink()
    forget_secrets(ctype, name)
    return True


def connect(name: str):
    """Instantiate the connector for a saved connection and record that it was used."""
    ctype, path = find(name)
    rec = _read(path)
    conn = REGISTRY[ctype](_resolve(ctype, name, rec["params"]))
    rec["last_used_at"] = _now()
    path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return conn


def record_test(name: str, ok: bool, summary: dict | None = None, error: str | None = None) -> None:
    _, path = find(name)
    rec = _read(path)
    rec["last_test"] = {"ok": ok, "at": _now(), **({"summary": summary} if summary else {}),
                        **({"error": error} if error else {})}
    path.write_text(json.dumps(rec, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- prompting
def _can_reprompt() -> bool:
    """True when a person is at the keyboard (so asking again makes sense)."""
    import sys
    return sys.stdin is not None and sys.stdin.isatty()


def ask(spec: ConnectorSpec, ask_fn: Callable[[str], str] = input,
        secret_fn: Callable[[str], str] = getpass.getpass,
        preset: dict | None = None, include_advanced: bool = False) -> tuple[dict, dict]:
    """Walk the spec's fields, asking only for those that apply (visible_when).

    Returns (params, secret_values). A secret typed here is returned in `secret_values`
    for the caller to store in the credential store; `params` only gets a reference."""
    answers: dict = dict(preset or {})
    secrets: dict = {}
    for f in spec.fields:
        if f.name in answers or not f.is_visible(spec.effective(answers)):
            continue
        if f.advanced and not include_advanced:
            continue
        hint = f" [{f.default}]" if f.default is not None else ""
        if f.options:
            hint += f" ({'/'.join(f.options)})"
        label = f"{f.label}{hint}"
        if f.help:
            label += f"\n   {f.help}"
        if f.secret:
            print(label)
            try:
                typed = secret_fn("   paste it here (hidden; saved to the credential store, "
                                  "blank = ask me every time): ")
            except EOFError:
                typed = ""
            if typed.strip():
                secrets[f.name] = typed.strip()
                answers[f.name] = "keyring:"
            else:
                answers[f.name] = "prompt:"
            continue
        attempts = 0
        while True:
            attempts += 1
            if attempts > 5:  # never loop forever on a value that cannot become valid
                raise ConnectorError(f"'{f.name}': too many invalid answers; "
                                     f"{f.pattern_hint or 'invalid value'}")
            try:
                v = ask_fn(f"{label}: ").strip()
            except EOFError:  # no terminal: only optional fields may be skipped
                if f.required and f.default is None:
                    raise ConnectorError(f"'{f.name}' is required (pass --set {f.name}=...)")
                v = ""
            if not v and f.default is not None:
                v = str(f.default)
            if v and f.pattern and not re.fullmatch(f.pattern, v):
                reason = f"'{f.name}': {f.pattern_hint or 'invalid value'}"
                if not _can_reprompt():  # no person to ask again: fail loudly, never drop it
                    raise ConnectorError(reason)
                print(f"   '{v}' is not accepted: {f.pattern_hint or 'invalid value'}")
                print("   Try again, or press Enter to leave it empty.")
                continue
            if v or not f.required:
                break
            print("   (required)")
        if v:
            answers[f.name] = v
    errors = spec.validate(answers)
    if errors:
        raise ConnectorError("; ".join(errors))
    return answers, secrets
