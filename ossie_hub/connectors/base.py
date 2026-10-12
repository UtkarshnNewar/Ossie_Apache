"""Connector interface and registry."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from ossie_hub.spec import ConnectorSpec

REGISTRY: dict[str, type["Connector"]] = {}


def register(cls: type["Connector"]) -> type["Connector"]:
    REGISTRY[cls.spec.type] = cls
    return cls


def resolve_secret(value: str | None) -> str | None:
    """Secrets are passed by reference ("env:NAME"), so they never sit in chat/logs/profiles."""
    if value == "prompt:":
        import getpass
        return getpass.getpass("Secret value (not stored): ")
    if value and value.startswith("env:"):
        got = os.environ.get(value[4:])
        if got is None:
            raise ValueError(f"environment variable {value[4:]} is not set")
        return got
    return value


class ConnectorError(Exception):
    pass


class Connector(ABC):
    spec: ConnectorSpec  # set on each subclass

    def __init__(self, params: dict[str, Any]):
        errors = self.spec.validate(params)
        if errors:
            raise ConnectorError("; ".join(errors))
        self.params = self.spec.effective(params)

    @abstractmethod
    def test(self) -> dict[str, Any]:
        """Authenticate and prove access. Returns a small, non-secret summary."""

    @abstractmethod
    def options(self, picker: str, **selected: str) -> list[dict[str, str]]:
        """Live choices for a picker, given earlier picks. [{id, name}, ...]"""

    @abstractmethod
    def read_model(self, **selected: str) -> tuple[dict, list[str]]:
        """Return (ossie_document, notes)."""
