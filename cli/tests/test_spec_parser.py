"""Tests for spec_parser — product.yaml parsing, validation, and breaking change detection."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from datameshy.lib.spec_parser import (
    BreakingChange,
    SpecValidationError,
    detect_breaking_changes,
    parse_and_validate,
)


# ---------------------------------------------------------------------------
# parse_and_validate
# ---------------------------------------------------------------------------


class TestParseAndValidate:
    """Tests for parse_and_validate()."""

    def test_valid_spec(self, sample_spec_file):
        """A valid product.yaml should parse without errors."""
        result = parse_and_validate(sample_spec_file)
        assert isinstance(result, dict)
        assert result["product"]["name"] == "customer_orders"
        assert result["product"]["domain"] == "sales"
        assert len(result["schema"]["columns"]) == 5
        assert result["schema_version"] == 1

    def test_minimal_valid_spec(self, minimal_spec_file):
        """A minimal spec with just required fields should pass validation."""
        result = parse_and_validate(minimal_spec_file)
        assert result["product"]["name"] == "test_product"
        assert result["product"]["domain"] == "test"
        assert len(result["schema"]["columns"]) >= 1

    def test_invalid_spec_missing_required_fields(self, invalid_spec_file):
        """A spec missing required fields should raise SpecValidationError."""
        with pytest.raises(SpecValidationError) as exc_info:
            parse_and_validate(invalid_spec_file)
        # Should mention missing fields
        assert "validation failed" in str(exc_info.value).lower() or "error" in str(exc_info.value).lower()
        assert len(exc_info.value.errors) > 0

    def test_file_not_found(self):
        """Non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_and_validate("/nonexistent/path/product.yaml")

    def test_invalid_yaml_syntax(self, tmp_path):
        """A file with invalid YAML syntax should raise SpecValidationError."""
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("product:\n  name: [\n  invalid", encoding="utf-8")
        with pytest.raises(SpecValidationError, match="YAML parse error"):
            parse_and_validate(str(bad_yaml))

    def test_non_mapping_yaml(self, tmp_path):
        """A YAML file that is not a mapping (e.g. a list) should raise SpecValidationError."""
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2", encoding="utf-8")
        with pytest.raises(SpecValidationError, match="YAML mapping"):
            parse_and_validate(str(list_yaml))

    def test_missing_schema_columns(self, tmp_path):
        """A spec with schema but no columns should fail validation."""
        spec = textwrap.dedent("""\
            product:
              name: test
              domain: test
              description: "test"
              owner: test@example.com
            schema:
              columns: []
            quality:
              rules:
                - name: r1
                  rule: "IsComplete 'id'"
            sla:
              refresh_frequency: daily
        """)
        path = tmp_path / "product.yaml"
        path.write_text(spec, encoding="utf-8")
        with pytest.raises(SpecValidationError):
            parse_and_validate(str(path))

    def test_column_without_name(self, tmp_path):
        """A column missing the required 'name' field should fail."""
        spec = textwrap.dedent("""\
            product:
              name: test
              domain: test
              description: "test"
              owner: test@example.com
            schema:
              columns:
                - type: string
            quality:
              rules:
                - name: r1
                  rule: "IsComplete 'id'"
            sla:
              refresh_frequency: daily
        """)
        path = tmp_path / "product.yaml"
        path.write_text(spec, encoding="utf-8")
        with pytest.raises(SpecValidationError):
            parse_and_validate(str(path))

    def test_quality_rules_referencing_valid_columns(self, sample_spec_file):
        """Valid spec with quality rules that reference existing columns should pass."""
        result = parse_and_validate(sample_spec_file)
        rules = result["quality"]["rules"]
        assert len(rules) == 4
        assert rules[0]["name"] == "order_id_complete"


# ---------------------------------------------------------------------------
# detect_breaking_changes
# ---------------------------------------------------------------------------


class TestDetectBreakingChanges:
    """Tests for detect_breaking_changes()."""

    def _make_spec(self, columns, schema_version=1):
        """Helper to create a minimal spec dict with given columns."""
        return {
            "schema_version": schema_version,
            "schema": {"columns": columns},
        }

    def test_no_changes(self, parsed_spec):
        """Identical specs should report zero breaking changes."""
        changes = detect_breaking_changes(parsed_spec, parsed_spec)
        assert changes == []

    def test_column_removed(self):
        """Removing a column should be flagged as a breaking change."""
        old = self._make_spec([
            {"name": "id", "type": "string"},
            {"name": "value", "type": "integer"},
        ])
        new = self._make_spec([
            {"name": "id", "type": "string"},
        ])
        changes = detect_breaking_changes(old, new)
        assert any(c.change_type == "column_removed" for c in changes)
        assert any(c.field == "schema.columns.value" for c in changes)

    def test_column_type_changed(self):
        """Changing a column type should be flagged as breaking."""
        old = self._make_spec([
            {"name": "id", "type": "string"},
        ])
        new = self._make_spec([
            {"name": "id", "type": "integer"},
        ])
        changes = detect_breaking_changes(old, new)
        assert any(c.change_type == "type_changed" for c in changes)
        type_change = [c for c in changes if c.change_type == "type_changed"][0]
        assert type_change.old_value == "string"
        assert type_change.new_value == "integer"

    def test_nullable_false_to_true(self):
        """Changing nullable from False to True should be flagged as breaking."""
        old = self._make_spec([
            {"name": "id", "type": "string", "nullable": False},
        ])
        new = self._make_spec([
            {"name": "id", "type": "string", "nullable": True},
        ])
        changes = detect_breaking_changes(old, new)
        assert any(c.change_type == "nullable_changed" for c in changes)

    def test_nullable_true_to_false_not_breaking(self):
        """Changing nullable from True to False (tightening) should NOT be flagged."""
        old = self._make_spec([
            {"name": "id", "type": "string", "nullable": True},
        ])
        new = self._make_spec([
            {"name": "id", "type": "string", "nullable": False},
        ])
        changes = detect_breaking_changes(old, new)
        assert not any(c.change_type == "nullable_changed" for c in changes)

    def test_adding_column_not_breaking(self):
        """Adding a new column should not be flagged as breaking."""
        old = self._make_spec([
            {"name": "id", "type": "string"},
        ])
        new = self._make_spec([
            {"name": "id", "type": "string"},
            {"name": "new_col", "type": "integer"},
        ])
        changes = detect_breaking_changes(old, new)
        assert not any(c.change_type == "column_removed" for c in changes)

    def test_description_change_not_breaking(self):
        """Changing a column description should not be flagged."""
        old = self._make_spec([
            {"name": "id", "type": "string", "description": "old"},
        ])
        new = self._make_spec([
            {"name": "id", "type": "string", "description": "new"},
        ])
        changes = detect_breaking_changes(old, new)
        assert changes == []

    def test_schema_version_not_bumped_on_breaking_change(self):
        """If breaking changes exist but schema_version is not bumped, flag it."""
        old = self._make_spec(
            [{"name": "id", "type": "string"}, {"name": "value", "type": "integer"}],
            schema_version=1,
        )
        new = self._make_spec(
            [{"name": "id", "type": "string"}],
            schema_version=1,
        )
        changes = detect_breaking_changes(old, new)
        assert any(c.change_type == "schema_version_not_bumped" for c in changes)

    def test_schema_version_bumped_no_warning(self):
        """If schema_version is bumped, no version warning even with breaking changes."""
        old = self._make_spec(
            [{"name": "id", "type": "string"}, {"name": "value", "type": "integer"}],
            schema_version=1,
        )
        new = self._make_spec(
            [{"name": "id", "type": "string"}],
            schema_version=2,
        )
        changes = detect_breaking_changes(old, new)
        assert not any(c.change_type == "schema_version_not_bumped" for c in changes)
        # Still should have column_removed
        assert any(c.change_type == "column_removed" for c in changes)

    def test_multiple_breaking_changes(self):
        """Multiple breaking changes should all be detected."""
        old = self._make_spec([
            {"name": "id", "type": "string", "nullable": False},
            {"name": "amount", "type": "integer"},
            {"name": "removed_col", "type": "string"},
        ], schema_version=1)
        new = self._make_spec([
            {"name": "id", "type": "string", "nullable": True},
            {"name": "amount", "type": "decimal(10,2)"},
        ], schema_version=1)
        changes = detect_breaking_changes(old, new)
        change_types = [c.change_type for c in changes]
        assert "column_removed" in change_types
        assert "type_changed" in change_types
        assert "nullable_changed" in change_types
        assert "schema_version_not_bumped" in change_types

    def test_empty_specs(self):
        """Two empty specs should report no breaking changes."""
        changes = detect_breaking_changes({}, {})
        assert changes == []

    def test_breaking_change_dataclass_fields(self):
        """Verify BreakingChange dataclass has all expected fields."""
        old = self._make_spec([{"name": "col1", "type": "string"}])
        new = self._make_spec([])
        changes = detect_breaking_changes(old, new)
        bc = changes[0]
        assert isinstance(bc, BreakingChange)
        assert bc.field == "schema.columns.col1"
        assert bc.change_type == "column_removed"
        assert bc.old_value is not None
        assert bc.new_value is None
        assert "col1" in bc.message
