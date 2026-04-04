"""Terraform runner — thin wrapper around the terraform CLI.

Security note: This module NEVER logs credential-adjacent content.
Any output line containing 'access_key', 'secret', or 'token' is
redacted before logging or returning to the caller.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Patterns that indicate credential-adjacent content — redact these lines.
_CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "access_key",
    "secret",
    "token",
)


class TerraformError(Exception):
    """Raised when a terraform command exits with a non-zero status."""

    def __init__(self, message: str, stderr_tail: list[str] | None = None) -> None:
        super().__init__(message)
        self.stderr_tail: list[str] = stderr_tail or []

    def __str__(self) -> str:
        base = super().__str__()
        if self.stderr_tail:
            tail = "\n  ".join(self.stderr_tail)
            return f"{base}\n  {tail}"
        return base


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _redact_line(line: str) -> str | None:
    """Return None if the line contains credential-adjacent content, else the line."""
    lower = line.lower()
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern in lower:
            return None
    return line


def _filter_output(text: str) -> str:
    """Filter credential-adjacent lines from terraform output."""
    filtered = []
    for line in text.splitlines():
        safe = _redact_line(line)
        if safe is not None:
            filtered.append(safe)
    return "\n".join(filtered)


def _run(
    args: list[str],
    cwd: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess, capturing stdout and stderr."""
    merged_env = {**os.environ}
    if env:
        merged_env.update(env)

    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def _build_var_flags(var_overrides: dict[str, Any]) -> list[str]:
    """Convert a dict of variable overrides to -var 'key=value' flags."""
    flags: list[str] = []
    for key, value in var_overrides.items():
        flags.extend(["-var", f"{key}={value}"])
    return flags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan(
    env_dir: str,
    var_overrides: dict[str, Any] | None = None,
    extra_env: dict[str, str] | None = None,
) -> str:
    """Run `terraform init && terraform plan -json` in *env_dir*.

    Args:
        env_dir: Path to the Terraform environment directory.
        var_overrides: Optional dict of variable overrides passed as -var flags.
        extra_env: Optional extra environment variables (e.g. AWS credentials from SSO session).

    Returns:
        Filtered plan JSON output as a string.

    Raises:
        TerraformError: If init or plan exits with a non-zero status.
        FileNotFoundError: If env_dir does not exist.
    """
    env_path = Path(env_dir)
    if not env_path.is_dir():
        raise FileNotFoundError(f"Terraform environment directory not found: {env_dir}")

    var_overrides = var_overrides or {}

    # Step 1: terraform init
    init_result = _run(["terraform", "init", "-no-color"], cwd=str(env_path), env=extra_env)
    if init_result.returncode != 0:
        stderr_lines = _filter_output(init_result.stderr).splitlines()
        raise TerraformError(
            f"terraform init failed in {env_dir} (exit code {init_result.returncode})",
            stderr_tail=stderr_lines[-20:],
        )

    # Step 2: terraform plan -json
    plan_args = ["terraform", "plan", "-json", "-no-color"] + _build_var_flags(var_overrides)
    plan_result = _run(plan_args, cwd=str(env_path), env=extra_env)
    if plan_result.returncode not in (0, 2):  # 2 = changes present (success)
        stderr_lines = _filter_output(plan_result.stderr).splitlines()
        raise TerraformError(
            f"terraform plan failed in {env_dir} (exit code {plan_result.returncode})",
            stderr_tail=stderr_lines[-20:],
        )

    return _filter_output(plan_result.stdout)


def apply(
    env_dir: str,
    var_overrides: dict[str, Any] | None = None,
    auto_approve: bool = False,
    extra_env: dict[str, str] | None = None,
) -> bool:
    """Run `terraform apply` in *env_dir*.

    Args:
        env_dir: Path to the Terraform environment directory.
        var_overrides: Optional dict of variable overrides passed as -var flags.
        auto_approve: If True, pass -auto-approve (no interactive prompt). Must be
            set explicitly — callers must opt in to automated applies.
        extra_env: Optional extra environment variables.

    Returns:
        True on success.

    Raises:
        TerraformError: If apply exits with a non-zero status.
        FileNotFoundError: If env_dir does not exist.
        ValueError: If auto_approve is False (interactive applies not supported in subprocess mode).
    """
    env_path = Path(env_dir)
    if not env_path.is_dir():
        raise FileNotFoundError(f"Terraform environment directory not found: {env_dir}")

    var_overrides = var_overrides or {}

    apply_args = ["terraform", "apply", "-no-color"] + _build_var_flags(var_overrides)
    if auto_approve:
        apply_args.append("-auto-approve")
    else:
        # Interactive apply is not supported in subprocess mode.
        # The CLI layer should prompt the user and only call apply(auto_approve=True).
        raise ValueError(
            "auto_approve must be True for non-interactive apply. "
            "Prompt the user before calling apply()."
        )

    result = _run(apply_args, cwd=str(env_path), env=extra_env)
    if result.returncode != 0:
        stderr_lines = _filter_output(result.stderr).splitlines()
        raise TerraformError(
            f"terraform apply failed in {env_dir} (exit code {result.returncode})",
            stderr_tail=stderr_lines[-20:],
        )

    return True
