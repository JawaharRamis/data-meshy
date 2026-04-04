"""Tests for terraform_runner — terraform plan/apply with credential filtering."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from datameshy.lib.terraform_runner import (
    TerraformError,
    _filter_output,
    _redact_line,
    apply,
    plan,
)


# ---------------------------------------------------------------------------
# _redact_line and _filter_output
# ---------------------------------------------------------------------------


class TestCredentialFiltering:
    """Tests for credential redaction in terraform output."""

    def test_redact_access_key_line(self):
        """Lines containing 'access_key' should be redacted."""
        assert _redact_line('aws_access_key_id = "AKIA12345"') is None

    def test_redact_secret_line(self):
        """Lines containing 'secret' should be redacted."""
        assert _redact_line('aws_secret_access_key = "abc123"') is None

    def test_redact_token_line(self):
        """Lines containing 'token' should be redacted."""
        assert _redact_line('session_token = "tok123"') is None

    def test_keep_normal_line(self):
        """Normal lines should not be redacted."""
        assert _redact_line('module.domain.aws_s3_bucket.raw') == 'module.domain.aws_s3_bucket.raw'

    def test_keep_plan_summary_line(self):
        """Plan summary lines should not be redacted."""
        line = "Plan: 3 to add, 0 to change, 0 to destroy."
        assert _redact_line(line) == line

    def test_filter_output_removes_credential_lines(self):
        """_filter_output should remove all credential-adjacent lines."""
        output = textwrap.dedent("""\
            normal line 1
            aws_access_key_id = "AKIA12345"
            another normal line
            secret_key = "abc"
            final line
        """)
        filtered = _filter_output(output)
        assert "access_key" not in filtered
        assert "secret" not in filtered.lower()
        assert "normal line 1" in filtered
        assert "another normal line" in filtered
        assert "final line" in filtered

    def test_filter_output_case_insensitive(self):
        """Credential filtering should be case-insensitive."""
        output = 'AWS_ACCESS_KEY = "test"\nNormal line'
        filtered = _filter_output(output)
        assert "AWS_ACCESS_KEY" not in filtered
        assert "Normal line" in filtered


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


class TestPlan:
    """Tests for terraform plan()."""

    def test_plan_nonexistent_directory(self):
        """Should raise FileNotFoundError for a non-existent directory."""
        with pytest.raises(FileNotFoundError, match="not found"):
            plan("/nonexistent/terraform/dir")

    def test_plan_init_failure(self, tmp_path):
        """Should raise TerraformError when terraform init fails."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: terraform init failed"

        with patch("datameshy.lib.terraform_runner._run", return_value=mock_result):
            with pytest.raises(TerraformError, match="terraform init failed"):
                plan(str(env_dir))

    def test_plan_success(self, tmp_path):
        """Should return filtered plan output on success."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        init_result = MagicMock()
        init_result.returncode = 0
        init_result.stdout = "Init complete"
        init_result.stderr = ""

        plan_result = MagicMock()
        plan_result.returncode = 0
        plan_result.stdout = '{"type":"change","change":{"action":"create"}}'
        plan_result.stderr = ""

        with patch("datameshy.lib.terraform_runner._run", side_effect=[init_result, plan_result]):
            output = plan(str(env_dir))
            assert "change" in output

    def test_plan_with_changes_exit_code_2(self, tmp_path):
        """Exit code 2 (changes present) should be treated as success."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        init_result = MagicMock()
        init_result.returncode = 0
        init_result.stdout = ""
        init_result.stderr = ""

        plan_result = MagicMock()
        plan_result.returncode = 2
        plan_result.stdout = "Plan: 3 to add"
        plan_result.stderr = ""

        with patch("datameshy.lib.terraform_runner._run", side_effect=[init_result, plan_result]):
            output = plan(str(env_dir))
            assert "Plan" in output

    def test_plan_filters_credentials(self, tmp_path):
        """Plan output should have credential lines filtered."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        init_result = MagicMock()
        init_result.returncode = 0
        init_result.stdout = ""
        init_result.stderr = ""

        plan_output = (
            'Plan: 1 to add\n'
            'aws_access_key = "AKIA12345"\n'
            'resource "aws_s3_bucket" "test"'
        )
        plan_result = MagicMock()
        plan_result.returncode = 0
        plan_result.stdout = plan_output
        plan_result.stderr = ""

        with patch("datameshy.lib.terraform_runner._run", side_effect=[init_result, plan_result]):
            output = plan(str(env_dir))
            assert "access_key" not in output
            assert "aws_s3_bucket" in output

    def test_plan_with_var_overrides(self, tmp_path):
        """Variable overrides should be passed as -var flags."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        init_result = MagicMock()
        init_result.returncode = 0
        init_result.stdout = ""
        init_result.stderr = ""

        plan_result = MagicMock()
        plan_result.returncode = 0
        plan_result.stdout = "Plan: 0 to add"
        plan_result.stderr = ""

        calls = []

        def mock_run(args, cwd, env=None):
            calls.append(args)
            if "init" in args:
                return init_result
            return plan_result

        with patch("datameshy.lib.terraform_runner._run", side_effect=mock_run):
            plan(str(env_dir), var_overrides={"domain": "sales", "account_id": "123"})

        # Check that plan call included -var flags
        plan_call = calls[1]
        assert "-var" in plan_call
        assert "domain=sales" in plan_call
        assert "account_id=123" in plan_call


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


class TestApply:
    """Tests for terraform apply()."""

    def test_apply_nonexistent_directory(self):
        """Should raise FileNotFoundError for non-existent directory."""
        with pytest.raises(FileNotFoundError):
            apply("/nonexistent/dir")

    def test_apply_requires_auto_approve(self, tmp_path):
        """Should raise ValueError if auto_approve is False."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        with pytest.raises(ValueError, match="auto_approve must be True"):
            apply(str(env_dir), auto_approve=False)

    def test_apply_success(self, tmp_path):
        """Should return True on successful apply."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Apply complete!"
        mock_result.stderr = ""

        with patch("datameshy.lib.terraform_runner._run", return_value=mock_result):
            result = apply(str(env_dir), auto_approve=True)
            assert result is True

    def test_apply_failure(self, tmp_path):
        """Should raise TerraformError on apply failure."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: something went wrong\nError details here"

        with patch("datameshy.lib.terraform_runner._run", return_value=mock_result):
            with pytest.raises(TerraformError, match="terraform apply failed"):
                apply(str(env_dir), auto_approve=True)

    def test_apply_includes_auto_approve_flag(self, tmp_path):
        """When auto_approve=True, -auto-approve should be in the command."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        calls = []

        def mock_run(args, cwd, env=None):
            calls.append(args)
            return mock_result

        with patch("datameshy.lib.terraform_runner._run", side_effect=mock_run):
            apply(str(env_dir), auto_approve=True)

        assert "-auto-approve" in calls[0]

    def test_apply_filters_credentials_from_stderr(self, tmp_path):
        """Credential lines in stderr should be filtered in the error."""
        env_dir = tmp_path / "env"
        env_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = 'aws_secret_key = "abc"\nError: apply failed'

        with patch("datameshy.lib.terraform_runner._run", return_value=mock_result):
            with pytest.raises(TerraformError) as exc_info:
                apply(str(env_dir), auto_approve=True)
            # The error message should not contain the secret
            assert "secret" not in str(exc_info.value).lower() or "secret_key" not in str(exc_info.value)
