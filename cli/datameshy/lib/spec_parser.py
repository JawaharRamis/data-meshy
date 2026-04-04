"""Product spec parser and validator.

Loads product.yaml files, validates them against the JSON Schema, and
detects breaking changes between two versions of a spec.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    import jsonschema
    from jsonschema import ValidationError as _JSValidationError
except ImportError as exc:  # pragma: no cover
    raise ImportError("jsonschema is required: pip install jsonschema>=4.21") from exc


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SpecValidationError(Exception):
    """Raised when a product.yaml does not satisfy the JSON Schema."""

    def __init__(self, message: str, errors: list[str] | None = None) -> None:
        super().__init__(message)
        self.errors: list[str] = errors or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.errors:
            detail = "\n  - " + "\n  - ".join(self.errors)
            return f"{base}{detail}"
        return base


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BreakingChange:
    """Represents a single breaking change detected between two spec versions."""

    field: str
    change_type: str  # "column_removed" | "type_changed" | "nullable_changed" | "schema_version_not_bumped"
    old_value: Any
    new_value: Any
    message: str


# ---------------------------------------------------------------------------
# Internal: JSON Schema (inline fallback)
# ---------------------------------------------------------------------------

# Minimal inline JSON Schema for product.yaml.
# In production this would live at schemas/product_spec.json alongside the repo.
# The CLI bundles this schema so that validation works offline.
_INLINE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["product", "schema", "quality", "sla"],
    "properties": {
        "product": {
            "type": "object",
            "required": ["name", "domain", "description", "owner"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "domain": {"type": "string", "minLength": 1},
                "description": {"type": "string"},
                "owner": {"type": "string"},
                "contact_channel": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "schema_version": {"type": "integer", "minimum": 1},
        "schema": {
            "type": "object",
            "required": ["columns"],
            "properties": {
                "format": {"type": "string"},
                "columns": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "type"],
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                            "pii": {"type": "boolean"},
                            "nullable": {"type": "boolean"},
                        },
                        "additionalProperties": True,
                    },
                },
                "partition_by": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["column"],
                        "properties": {
                            "column": {"type": "string"},
                            "transform": {"type": "string"},
                        },
                    },
                },
            },
            "additionalProperties": True,
        },
        "quality": {
            "type": "object",
            "required": ["rules"],
            "properties": {
                "rules": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "rule"],
                        "properties": {
                            "name": {"type": "string"},
                            "rule": {"type": "string"},
                            "threshold": {"type": "number"},
                        },
                        "additionalProperties": True,
                    },
                },
                "minimum_quality_score": {"type": "number"},
            },
            "additionalProperties": True,
        },
        "sla": {
            "type": "object",
            "required": ["refresh_frequency"],
            "properties": {
                "refresh_frequency": {"type": "string"},
                "freshness_target": {"type": "string"},
                "availability": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "classification": {"type": "string"},
        "lineage": {
            "type": "object",
            "properties": {
                "sources": {"type": "array"},
            },
            "additionalProperties": True,
        },
    },
    "additionalProperties": True,
}


def _load_schema(schema_path: str | None = None) -> dict[str, Any]:
    """Load JSON Schema from file if provided, otherwise use the inline schema."""
    if schema_path and os.path.isfile(schema_path):
        import json
        with open(schema_path, encoding="utf-8") as fh:
            return json.load(fh)

    # Try to locate schemas/product_spec.json relative to repo root
    # (walks up from this file looking for schemas/)
    current = Path(__file__).resolve()
    for _ in range(10):
        candidate = current / "schemas" / "product_spec.json"
        if candidate.is_file():
            import json
            with open(candidate, encoding="utf-8") as fh:
                return json.load(fh)
        current = current.parent
        if current == current.parent:
            break

    return _INLINE_SCHEMA


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_and_validate(spec_path: str, schema_path: str | None = None) -> dict[str, Any]:
    """Load a product.yaml, validate it against the JSON Schema, and return the parsed dict.

    Args:
        spec_path: Path to the product.yaml file.
        schema_path: Optional path to a custom JSON Schema file. Defaults to the
            bundled inline schema (or schemas/product_spec.json if found in the repo).

    Returns:
        Parsed and validated product spec as a dict.

    Raises:
        FileNotFoundError: If spec_path does not exist.
        SpecValidationError: If the spec fails JSON Schema validation.
    """
    path = Path(spec_path)
    if not path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    with open(path, encoding="utf-8") as fh:
        try:
            spec: dict[str, Any] = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise SpecValidationError(f"YAML parse error in {spec_path}: {exc}") from exc

    if not isinstance(spec, dict):
        raise SpecValidationError(f"product.yaml must be a YAML mapping (got {type(spec).__name__})")

    schema = _load_schema(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(spec), key=lambda e: str(e.path))

    if errors:
        messages = []
        for err in errors:
            path_str = " > ".join(str(p) for p in err.absolute_path) if err.absolute_path else "(root)"
            messages.append(f"{path_str}: {err.message}")
        raise SpecValidationError(
            f"Spec validation failed for {spec_path} ({len(messages)} error(s))",
            errors=messages,
        )

    return spec


def detect_breaking_changes(old_spec: dict[str, Any], new_spec: dict[str, Any]) -> list[BreakingChange]:
    """Compare two product specs and return a list of breaking changes.

    Breaking changes are:
    - A column was removed
    - A column's type changed
    - A column's nullable flag changed from False to True (tightening)
    - schema_version was not bumped when any breaking change was detected

    Args:
        old_spec: Previously released product spec dict.
        new_spec: Proposed new product spec dict.

    Returns:
        List of BreakingChange objects. Empty list means no breaking changes.
    """
    breaking: list[BreakingChange] = []

    old_columns: dict[str, dict] = {
        col["name"]: col for col in old_spec.get("schema", {}).get("columns", [])
    }
    new_columns: dict[str, dict] = {
        col["name"]: col for col in new_spec.get("schema", {}).get("columns", [])
    }

    # 1. Column removed
    for col_name, old_col in old_columns.items():
        if col_name not in new_columns:
            breaking.append(
                BreakingChange(
                    field=f"schema.columns.{col_name}",
                    change_type="column_removed",
                    old_value=old_col,
                    new_value=None,
                    message=f"Column '{col_name}' was removed from the schema.",
                )
            )

    for col_name, old_col in old_columns.items():
        if col_name not in new_columns:
            continue
        new_col = new_columns[col_name]

        # 2. Type changed
        old_type = old_col.get("type")
        new_type = new_col.get("type")
        if old_type and new_type and old_type != new_type:
            breaking.append(
                BreakingChange(
                    field=f"schema.columns.{col_name}.type",
                    change_type="type_changed",
                    old_value=old_type,
                    new_value=new_type,
                    message=f"Column '{col_name}' type changed from '{old_type}' to '{new_type}'.",
                )
            )

        # 3. Nullable changed from False → True (making a required field optional = breaking for consumers)
        old_nullable = old_col.get("nullable")
        new_nullable = new_col.get("nullable")
        if old_nullable is False and new_nullable is True:
            breaking.append(
                BreakingChange(
                    field=f"schema.columns.{col_name}.nullable",
                    change_type="nullable_changed",
                    old_value=old_nullable,
                    new_value=new_nullable,
                    message=(
                        f"Column '{col_name}' nullable changed from False to True "
                        "(consumers expecting non-null values will encounter nulls)."
                    ),
                )
            )

    # 4. schema_version not bumped when there are breaking changes
    if breaking:
        old_version = old_spec.get("schema_version", 0)
        new_version = new_spec.get("schema_version", 0)
        if new_version <= old_version:
            breaking.append(
                BreakingChange(
                    field="schema_version",
                    change_type="schema_version_not_bumped",
                    old_value=old_version,
                    new_value=new_version,
                    message=(
                        f"schema_version must be incremented when introducing breaking changes "
                        f"(current: {new_version}, previous: {old_version})."
                    ),
                )
            )

    return breaking
