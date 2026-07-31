"""Strict JSON-compatible value types used at persistence/API boundaries."""
from __future__ import annotations

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
