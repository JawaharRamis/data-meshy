"""Tests for domain init and domain upgrade CLI commands."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from datameshy.commands.domain import app, _validate_domain_name

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scaffold(tmp_path: Path, name: str = "test-domain", platform_version: str = "1.0.0") -> Path:
    """Run domain init and return the scaffolded repo root."""
    result = runner.invoke(
        app,
        [
            "init",
            "--name", name,
            "--account-id", "123456789012",
            "--owner", "owner@example.com",
            "--platform-version", platform_version,
            "--output-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, f"domain init failed:\n{result.output}"
    return tmp_path / f"data-meshy-{name}"


# ---------------------------------------------------------------------------
# domain init: file structure
# ---------------------------------------------------------------------------


class TestDomainInit:
    """Tests for `datameshy domain init`."""

    EXPECTED_FILES = [
        ".datameshy.toml",
        "README.md",
        "infra/backend.tf",
        "infra/main.tf",
        "infra/variables.tf",
        "infra/outputs.tf",
        "infra/terraform.tfvars",
        ".github/workflows/infra-plan.yml",
        ".github/workflows/infra-apply.yml",
        ".github/workflows/product-validate.yml",
    ]

    def test_scaffolds_expected_files(self, tmp_path):
        """All expected files must be created."""
        repo = _scaffold(tmp_path)
        for rel in self.EXPECTED_FILES:
            assert (repo / rel).is_file(), f"Missing file: {rel}"

    def test_datameshy_toml_content(self, tmp_path):
        """The .datameshy.toml must follow the shared contract schema."""
        import tomllib

        repo = _scaffold(tmp_path, name="finance", platform_version="1.2.3")
        toml_path = repo / ".datameshy.toml"
        config = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        assert config["platform"]["version"] == "1.2.3"
        assert config["platform"]["repo"] == "JawaharRamis/data-meshy-platform"
        assert config["domain"]["name"] == "finance"
        assert config["domain"]["account_id"] == "123456789012"
        assert config["domain"]["owner"] == "owner@example.com"

    def test_main_tf_git_source(self, tmp_path):
        """infra/main.tf must use the agreed git:: module source format."""
        repo = _scaffold(tmp_path, platform_version="1.0.0")
        main_tf = (repo / "infra/main.tf").read_text(encoding="utf-8")
        assert "git::https://github.com/JawaharRamis/data-meshy-platform.git" in main_tf
        assert "?ref=v1.0.0" in main_tf

    def test_main_tf_module_name(self, tmp_path):
        """infra/main.tf must reference the domain-account module."""
        repo = _scaffold(tmp_path)
        main_tf = (repo / "infra/main.tf").read_text(encoding="utf-8")
        assert "infra/modules/domain-account" in main_tf

    def test_backend_tf_state_key(self, tmp_path):
        """infra/backend.tf must use domain name in the state key."""
        repo = _scaffold(tmp_path, name="marketing")
        backend_tf = (repo / "infra/backend.tf").read_text(encoding="utf-8")
        assert 'key    = "marketing/terraform.tfstate"' in backend_tf

    def test_workflow_files_use_platform_version(self, tmp_path):
        """Workflow files must reference the pinned platform version."""
        repo = _scaffold(tmp_path, platform_version="2.0.0")
        for wf in [
            ".github/workflows/infra-plan.yml",
            ".github/workflows/infra-apply.yml",
            ".github/workflows/product-validate.yml",
        ]:
            content = (repo / wf).read_text(encoding="utf-8")
            assert "@v2.0.0" in content, f"{wf} missing @v2.0.0"

    def test_tfvars_contains_domain_values(self, tmp_path):
        """infra/terraform.tfvars must include domain name and account_id."""
        repo = _scaffold(tmp_path, name="ops")
        tfvars = (repo / "infra/terraform.tfvars").read_text(encoding="utf-8")
        assert 'domain     = "ops"' in tfvars
        assert 'account_id = "123456789012"' in tfvars

    def test_readme_mentions_domain(self, tmp_path):
        """README.md must mention the domain name."""
        repo = _scaffold(tmp_path, name="logistics")
        readme = (repo / "README.md").read_text(encoding="utf-8")
        assert "logistics" in readme

    def test_output_dir_default_is_cwd(self, tmp_path, monkeypatch):
        """Without --output-dir, files are scaffolded into the CWD."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "init",
                "--name", "cwd-domain",
                "--account-id", "123456789012",
                "--owner", "owner@example.com",
            ],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "data-meshy-cwd-domain" / ".datameshy.toml").is_file()

    def test_invalid_domain_name_raises(self, tmp_path):
        """An invalid domain name (starts with hyphen) should fail."""
        result = runner.invoke(
            app,
            [
                "init",
                "--name", "-bad-name",
                "--account-id", "123456789012",
                "--owner", "owner@example.com",
                "--output-dir", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_invalid_account_id_raises(self, tmp_path):
        """A non-12-digit account ID should fail."""
        result = runner.invoke(
            app,
            [
                "init",
                "--name", "valid-name",
                "--account-id", "1234",
                "--owner", "owner@example.com",
                "--output-dir", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_invalid_owner_raises(self, tmp_path):
        """An owner without @ should fail."""
        result = runner.invoke(
            app,
            [
                "init",
                "--name", "valid-name",
                "--account-id", "123456789012",
                "--owner", "not-an-email",
                "--output-dir", str(tmp_path),
            ],
        )
        assert result.exit_code != 0

    def test_invalid_platform_version_raises(self, tmp_path):
        """A non-semver platform version should fail."""
        result = runner.invoke(
            app,
            [
                "init",
                "--name", "valid-name",
                "--account-id", "123456789012",
                "--owner", "owner@example.com",
                "--platform-version", "not-semver",
                "--output-dir", str(tmp_path),
            ],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# domain upgrade
# ---------------------------------------------------------------------------


def _make_domain_repo(tmp_path: Path, version: str = "1.0.0", name: str = "sales") -> Path:
    """Create a minimal domain repo structure for upgrade tests."""
    repo = tmp_path / f"data-meshy-{name}"

    (repo / "infra").mkdir(parents=True)
    (repo / ".github" / "workflows").mkdir(parents=True)

    (repo / ".datameshy.toml").write_text(
        textwrap.dedent(f"""\
            [platform]
            version = "{version}"
            repo = "JawaharRamis/data-meshy-platform"

            [domain]
            name = "{name}"
            account_id = "123456789012"
            aws_region = "us-east-1"
            owner = "owner@example.com"
        """),
        encoding="utf-8",
    )

    (repo / "infra" / "main.tf").write_text(
        textwrap.dedent(f"""\
            module "domain_account" {{
              source = "git::https://github.com/JawaharRamis/data-meshy-platform.git//infra/modules/domain-account?ref=v{version}"
            }}
        """),
        encoding="utf-8",
    )

    for wf_name in ["infra-plan.yml", "infra-apply.yml", "product-validate.yml"]:
        (repo / ".github" / "workflows" / wf_name).write_text(
            textwrap.dedent(f"""\
                jobs:
                  plan:
                    uses: JawaharRamis/data-meshy-platform/.github/workflows/reusable-infra-plan.yml@v{version}
            """),
            encoding="utf-8",
        )

    return repo


class TestDomainUpgrade:
    """Tests for `datameshy domain upgrade`."""

    def test_updates_toml_version(self, tmp_path):
        """After upgrade, .datameshy.toml should contain the new version."""
        import tomllib

        repo = _make_domain_repo(tmp_path, version="1.0.0")
        result = runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        assert result.exit_code == 0, result.output

        config = tomllib.loads((repo / ".datameshy.toml").read_text(encoding="utf-8"))
        assert config["platform"]["version"] == "1.1.0"

    def test_updates_main_tf_ref(self, tmp_path):
        """After upgrade, infra/main.tf should have the new ?ref= value."""
        repo = _make_domain_repo(tmp_path, version="1.0.0")
        result = runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        assert result.exit_code == 0, result.output
        main_tf = (repo / "infra" / "main.tf").read_text(encoding="utf-8")
        assert "?ref=v1.1.0" in main_tf
        assert "?ref=v1.0.0" not in main_tf

    def test_updates_workflow_versions(self, tmp_path):
        """After upgrade, all workflow files should reference the new version."""
        repo = _make_domain_repo(tmp_path, version="1.0.0")
        result = runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        assert result.exit_code == 0, result.output
        for wf_name in ["infra-plan.yml", "infra-apply.yml", "product-validate.yml"]:
            content = (repo / ".github" / "workflows" / wf_name).read_text(encoding="utf-8")
            assert "@v1.1.0" in content, f"{wf_name} not updated"
            assert "@v1.0.0" not in content

    def test_idempotent_when_run_twice(self, tmp_path):
        """Running upgrade twice produces the same result (idempotent)."""
        import tomllib

        repo = _make_domain_repo(tmp_path, version="1.0.0")
        runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        result2 = runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        assert result2.exit_code == 0

        config = tomllib.loads((repo / ".datameshy.toml").read_text(encoding="utf-8"))
        assert config["platform"]["version"] == "1.1.0"

    def test_already_at_version_exits_cleanly(self, tmp_path):
        """If already at the target version, upgrade should exit cleanly."""
        repo = _make_domain_repo(tmp_path, version="2.0.0")
        result = runner.invoke(app, ["upgrade", "--to", "2.0.0", "--dir", str(repo)])
        assert result.exit_code == 0
        assert "nothing to do" in result.output.lower() or "already" in result.output.lower()

    def test_missing_toml_errors(self, tmp_path):
        """Upgrade on a non-datameshy directory should exit with code 1."""
        repo = tmp_path / "not-a-domain-repo"
        repo.mkdir()
        result = runner.invoke(app, ["upgrade", "--to", "1.1.0", "--dir", str(repo)])
        assert result.exit_code == 1
        assert "datameshy" in result.output.lower() or ".datameshy.toml" in result.output

    def test_invalid_to_version_raises(self, tmp_path):
        """A non-semver --to version should fail before touching files."""
        repo = _make_domain_repo(tmp_path, version="1.0.0")
        result = runner.invoke(app, ["upgrade", "--to", "not-a-version", "--dir", str(repo)])
        assert result.exit_code != 0
        # File should be untouched
        import tomllib
        config = tomllib.loads((repo / ".datameshy.toml").read_text(encoding="utf-8"))
        assert config["platform"]["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# _validate_domain_name unit tests
# ---------------------------------------------------------------------------


class TestValidateDomainName:
    """Tests for the domain name validator."""

    @pytest.mark.parametrize("name", [
        "sales",
        "sales-data",
        "a1",
        "domain-with-hyphens",
    ])
    def test_valid_names(self, name):
        """Valid domain names should return the name unchanged."""
        assert _validate_domain_name(name) == name

    @pytest.mark.parametrize("name", [
        "-invalid",
        "invalid-",
        "has spaces",
        "a" * 33,  # too long
        "has_underscore",
    ])
    def test_invalid_names_raise(self, name):
        """Invalid domain names should raise BadParameter."""
        with pytest.raises(typer.BadParameter):
            _validate_domain_name(name)
