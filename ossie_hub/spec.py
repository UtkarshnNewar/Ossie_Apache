"""Declarative connector specs.

A connector describes the information it needs as data (fields + dependent pickers).
UIs render forms from it, the MCP server derives tool schemas from it, and `validate`
checks user input - one source of truth for all three.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Field:
    name: str
    label: str
    type: str = "string"            # string | select | int | bool
    required: bool = False
    default: Any = None
    options: list[str] | None = None  # for type == "select"
    help: str = ""
    secret: bool = False            # never echoed, logged or stored in profiles
    visible_when: dict[str, list[str]] | None = None  # {other_field: [values]}
    pattern: str | None = None      # a regex the value must match (full match)
    pattern_hint: str = ""          # plain-language explanation shown when it does not
    advanced: bool = False          # rarely needed: hidden unless the user asks for advanced options

    def is_visible(self, params: dict[str, Any]) -> bool:
        if not self.visible_when:
            return True
        return all(str(params.get(k, "")) in vs for k, vs in self.visible_when.items())


@dataclass
class Picker:
    """A choice whose options come from the live service (e.g. workspace -> model)."""
    name: str
    label: str
    depends_on: list[str] = field(default_factory=list)
    required: bool = True


@dataclass
class ConnectorSpec:
    type: str
    title: str
    description: str
    fields: list[Field]
    pickers: list[Picker] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)

    def effective(self, params: dict[str, Any]) -> dict[str, Any]:
        out = {f.name: f.default for f in self.fields if f.default is not None}
        out.update({k: v for k, v in params.items() if v not in (None, "")})
        return out

    def is_field_required(self, name: str, params: dict[str, Any]) -> bool:
        f = next((x for x in self.fields if x.name == name), None)
        return bool(f and f.required and f.is_visible(self.effective(params)))

    def validate(self, params: dict[str, Any]) -> list[str]:
        p = self.effective(params)
        errors = []
        known = {f.name for f in self.fields}
        for k in params:
            if k not in known:
                errors.append(f"unknown field '{k}'")
        for f in self.fields:
            if not f.is_visible(p):
                continue
            v = p.get(f.name)
            if f.required and v in (None, ""):
                errors.append(f"'{f.name}' is required")
            if v not in (None, "") and f.type == "select" and f.options and v not in f.options:
                errors.append(f"'{f.name}' must be one of {f.options}")
            if v not in (None, "") and f.pattern and not re.fullmatch(f.pattern, str(v)):
                errors.append(f"'{f.name}': {f.pattern_hint or 'invalid value'}")
        return errors

    def form(self) -> dict[str, Any]:
        """JSON description for a UI / MCP client. Contains no secrets."""
        return {
            "type": self.type, "title": self.title, "description": self.description,
            "capabilities": self.capabilities,
            "fields": [{
                "name": f.name, "label": f.label, "type": f.type, "required": f.required,
                "default": f.default, "options": f.options, "help": f.help, "secret": f.secret, "pattern": f.pattern, "advanced": f.advanced,
                "visible_when": f.visible_when} for f in self.fields],
            "pickers": [{"name": p.name, "label": p.label, "depends_on": p.depends_on,
                         "required": p.required} for p in self.pickers],
        }

    def json_schema(self) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for f in self.fields:
            s: dict[str, Any] = {"title": f.label, "description": f.help}
            s["type"] = {"int": "integer", "bool": "boolean"}.get(f.type, "string")
            if f.options:
                s["enum"] = f.options
            if f.default is not None:
                s["default"] = f.default
            if f.secret:
                s["writeOnly"] = True
            props[f.name] = s
        return {"type": "object", "properties": props,
                "required": [f.name for f in self.fields if f.required and not f.visible_when],
                "additionalProperties": False}
