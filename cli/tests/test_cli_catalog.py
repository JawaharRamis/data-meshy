"""Tests for CLI catalog commands — search, browse, describe.

Acceptance criteria (Issue #12):
  AC1: catalog search --keyword <term> returns matching products
  AC2: catalog search --domain <name> filters by domain GSI
  AC3: catalog search --tag <key>=<value> filters by tag GSI
  AC4: catalog search --classification <level> filters by classification GSI
  AC5: catalog browse lists all products grouped by domain
  AC6: catalog describe <domain>/<product> returns full metadata
  AC7: catalog command group is registered in the CLI
  AC8: help text for all subcommands is available
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from datameshy.cli import app
from datameshy.lib.aws_client import APIError

runner = CliRunner()


def _invoke(*args, **kwargs):
    """Helper to invoke CLI with the runner."""
    return runner.invoke(app, *args, **kwargs)


def _mock_session() -> MagicMock:
    """Build a minimal MagicMock boto3 session."""
    session = MagicMock()
    session.client.return_value.get_caller_identity.return_value = {"Account": "123456789012"}
    session.region_name = "us-east-1"
    return session


def _patch_session(session: MagicMock):
    """Patch aws_client.get_session to return the given mock session."""
    from datameshy.lib import aws_client

    return patch.object(aws_client, "get_session", return_value=session)


def _patch_signed_request(return_value: dict):
    """Patch make_signed_request to return a fixed dict."""
    return patch(
        "datameshy.lib.aws_client.make_signed_request",
        return_value=return_value,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PRODUCT = {
    "product_id": "sales#customer_orders",
    "domain": "sales",
    "product_name": "customer_orders",
    "status": "ACTIVE",
    "description": "Customer orders data product",
    "tags": ["ecommerce", "transactions"],
    "classification": "internal",
    "owner": "sales-team@example.com",
    "quality_score": 98.5,
    "schema": {
        "columns": [
            {"name": "order_id", "type": "string", "pii": False},
            {"name": "customer_email", "type": "string", "pii": True},
        ]
    },
    "sla": {"refresh_frequency": "daily"},
    "subscriber_count": 3,
    "last_refreshed_at": "2026-05-01T00:00:00Z",
}

_SAMPLE_DEPRECATED = {
    "product_id": "sales#old_orders",
    "domain": "sales",
    "product_name": "old_orders",
    "status": "DEPRECATED",
    "description": "Legacy orders — use customer_orders instead",
    "tags": [],
    "classification": "internal",
    "owner": "sales-team@example.com",
    "quality_score": 70.0,
}


# ---------------------------------------------------------------------------
# AC1: catalog search --keyword
# ---------------------------------------------------------------------------


class TestCatalogSearchKeyword:
    """AC1: search --keyword returns matching products."""

    def test_search_keyword_happy_path(self):
        """search --keyword orders returns products matching the term."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--keyword", "orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        # Rich may truncate long names with ellipsis — check prefix
        assert "customer_ord" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "GET"
        assert "/catalog/search" in kwargs["url"]
        assert kwargs["params"]["keyword"] == "orders"

    def test_search_keyword_no_results(self):
        """search --keyword with no matches shows an informative message."""
        session = _mock_session()
        api_response = {"items": []}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "search",
                "--keyword", "nonexistent",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "no" in result.output.lower() or "0" in result.output

    def test_search_keyword_orders_returns_customer_orders(self):
        """AC10 smoke: --keyword orders must return customer_orders (full metadata)."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "search",
                "--keyword", "orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        # Rich may truncate long names — check prefix
        assert "customer_ord" in result.output
        assert "ACTIVE" in result.output

    def test_search_keyword_shows_deprecated_products(self):
        """Deprecated products must appear in search results (not filtered out)."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_DEPRECATED]}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "search",
                "--keyword", "orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "DEPRECATED" in result.output


# ---------------------------------------------------------------------------
# AC2: catalog search --domain
# ---------------------------------------------------------------------------


class TestCatalogSearchDomain:
    """AC2: search --domain filters by domain GSI."""

    def test_search_domain_happy_path(self):
        """search --domain sales uses domain GSI query param."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--domain", "sales",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        # Rich may truncate long names — check prefix
        assert "customer_ord" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["params"]["domain"] == "sales"

    def test_search_domain_empty(self):
        """search --domain with no results shows informative message."""
        session = _mock_session()
        api_response = {"items": []}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "search",
                "--domain", "marketing",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# AC3: catalog search --tag
# ---------------------------------------------------------------------------


class TestCatalogSearchTag:
    """AC3: search --tag <key>=<value> filters by tag GSI."""

    def test_search_tag_happy_path(self):
        """search --tag env=prod passes tag as query param."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--tag", "env=prod",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output

        _, kwargs = mock_req.call_args
        assert kwargs["params"]["tag"] == "env=prod"

    def test_search_tag_key_only(self):
        """search --tag ecommerce (no value) is passed as-is."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--tag", "ecommerce",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        _, kwargs = mock_req.call_args
        assert kwargs["params"]["tag"] == "ecommerce"


# ---------------------------------------------------------------------------
# AC4: catalog search --classification
# ---------------------------------------------------------------------------


class TestCatalogSearchClassification:
    """AC4: search --classification filters by classification GSI."""

    def test_search_classification_happy_path(self):
        """search --classification internal uses classification GSI query param."""
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--classification", "internal",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output

        _, kwargs = mock_req.call_args
        assert kwargs["params"]["classification"] == "internal"

    def test_search_classification_confidential(self):
        """search --classification confidential is passed correctly."""
        session = _mock_session()
        api_response = {"items": []}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "search",
                "--classification", "confidential",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0
        _, kwargs = mock_req.call_args
        assert kwargs["params"]["classification"] == "confidential"


# ---------------------------------------------------------------------------
# AC5: catalog browse
# ---------------------------------------------------------------------------


class TestCatalogBrowse:
    """AC5: browse lists all products grouped by domain."""

    def test_browse_happy_path(self):
        """browse returns products grouped by domain."""
        session = _mock_session()
        api_response = {
            "domains": {
                "sales": [_SAMPLE_PRODUCT],
                "finance": [
                    {
                        "product_id": "finance#invoices",
                        "domain": "finance",
                        "product_name": "invoices",
                        "status": "ACTIVE",
                        "description": "Finance invoices",
                        "classification": "internal",
                        "quality_score": 95.0,
                    }
                ],
            }
        }

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "browse",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "sales" in result.output
        # Rich may truncate long product names — check prefix
        assert "customer_ord" in result.output
        assert "finance" in result.output
        assert "invoices" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "GET"
        assert "/catalog/browse" in kwargs["url"]

    def test_browse_shows_deprecated_and_retired(self):
        """Browse must display DEPRECATED and RETIRED products, not filter them."""
        session = _mock_session()
        api_response = {
            "domains": {
                "sales": [_SAMPLE_PRODUCT, _SAMPLE_DEPRECATED],
            }
        }

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "browse",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "DEPRECATED" in result.output

    def test_browse_empty_catalog(self):
        """Browse with empty catalog shows informative message."""
        session = _mock_session()
        api_response = {"domains": {}}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "browse",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "no" in result.output.lower() or "empty" in result.output.lower() or "0" in result.output

    def test_browse_paginates_with_next_token(self):
        """Browse follows next_token for pagination."""
        session = _mock_session()
        page1 = {
            "domains": {"sales": [_SAMPLE_PRODUCT]},
            "next_token": "tok-abc",
        }
        page2 = {
            "domains": {"finance": [
                {
                    "product_id": "finance#invoices",
                    "domain": "finance",
                    "product_name": "invoices",
                    "status": "ACTIVE",
                    "description": "Finance invoices",
                    "classification": "internal",
                    "quality_score": 95.0,
                }
            ]},
        }

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=[page1, page2],
            ) as mock_req:
                result = _invoke([
                    "catalog", "browse",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0, result.output
        # Rich may truncate long product names — check prefix
        assert "customer_ord" in result.output
        assert "invoices" in result.output
        assert mock_req.call_count == 2


# ---------------------------------------------------------------------------
# AC6: catalog describe
# ---------------------------------------------------------------------------


class TestCatalogDescribe:
    """AC6: describe <domain>/<product> returns full metadata."""

    def test_describe_happy_path(self):
        """describe sales/customer_orders returns full product metadata."""
        session = _mock_session()
        api_response = _SAMPLE_PRODUCT

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "catalog", "describe", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        # Full metadata fields
        assert "customer_orders" in result.output
        assert "ACTIVE" in result.output
        assert "sales" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "GET"
        assert "/catalog/sales/customer_orders" in kwargs["url"]

    def test_describe_shows_schema(self):
        """describe output includes schema/columns."""
        session = _mock_session()
        api_response = _SAMPLE_PRODUCT

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "describe", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "order_id" in result.output

    def test_describe_shows_quality_score(self):
        """describe output includes quality score."""
        session = _mock_session()
        api_response = _SAMPLE_PRODUCT

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "describe", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "98" in result.output  # quality score 98.5

    def test_describe_shows_subscriber_count(self):
        """describe output includes subscriber count."""
        session = _mock_session()
        api_response = _SAMPLE_PRODUCT

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "describe", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "3" in result.output  # subscriber_count

    def test_describe_shows_sla(self):
        """describe output includes SLA information."""
        session = _mock_session()
        api_response = _SAMPLE_PRODUCT

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "describe", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "daily" in result.output

    def test_describe_deprecated_product(self):
        """describe a DEPRECATED product shows the deprecated status prominently."""
        session = _mock_session()
        api_response = _SAMPLE_DEPRECATED

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "describe", "sales/old_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "DEPRECATED" in result.output

    def test_describe_retired_product(self):
        """describe a RETIRED product displays the RETIRED status."""
        session = _mock_session()
        retired_product = {**_SAMPLE_PRODUCT, "status": "RETIRED", "product_name": "ancient_orders"}

        with _patch_session(session), _patch_signed_request(retired_product):
            result = _invoke([
                "catalog", "describe", "sales/ancient_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "RETIRED" in result.output

    def test_describe_product_not_found(self):
        """describe with 404 shows product-not-found message."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Not found", status_code=404),
            ):
                result = _invoke([
                    "catalog", "describe", "sales/does_not_exist",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0

    def test_describe_requires_slash_format(self):
        """describe requires <domain>/<product> format."""
        result = _invoke([
            "catalog", "describe", "invaliddomain",
            "--api-url", "https://api.example.com/prod",
        ])
        # Should show an error about format
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# AC7 & AC8: catalog command group registration and help
# ---------------------------------------------------------------------------


class TestCatalogHelp:
    """AC7/AC8: catalog command group is registered and shows subcommands."""

    def test_catalog_group_registered(self):
        """datameshy --help shows catalog as a command group."""
        result = _invoke(["--help"])
        assert result.exit_code == 0
        assert "catalog" in result.output

    def test_catalog_help_shows_subcommands(self):
        """datameshy catalog --help shows search, browse, describe."""
        result = _invoke(["catalog", "--help"])
        assert result.exit_code == 0
        for cmd in ("search", "browse", "describe"):
            assert cmd in result.output

    def test_catalog_search_help(self):
        """catalog search --help exits cleanly."""
        result = _invoke(["catalog", "search", "--help"])
        assert result.exit_code == 0

    def test_catalog_browse_help(self):
        """catalog browse --help exits cleanly."""
        result = _invoke(["catalog", "browse", "--help"])
        assert result.exit_code == 0

    def test_catalog_describe_help(self):
        """catalog describe --help exits cleanly."""
        result = _invoke(["catalog", "describe", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# API URL resolution (same pattern as subscribe)
# ---------------------------------------------------------------------------


class TestCatalogApiUrlResolution:
    """catalog commands inherit API URL resolution pattern from subscribe."""

    def test_api_url_from_env_var(self, monkeypatch):
        """DATAMESHY_API_URL env var is used when --api-url is absent."""
        monkeypatch.setenv("DATAMESHY_API_URL", "https://env-api.example.com/prod")
        session = _mock_session()
        api_response = {"items": [_SAMPLE_PRODUCT]}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "catalog", "search",
                "--keyword", "orders",
            ])

        assert result.exit_code == 0, result.output

    def test_no_api_url_exits_with_message(self, monkeypatch):
        """Missing API URL exits with helpful message."""
        monkeypatch.delenv("DATAMESHY_API_URL", raising=False)
        session = _mock_session()

        with _patch_session(session):
            result = _invoke([
                "catalog", "search",
                "--keyword", "orders",
            ])

        assert result.exit_code != 0

    def test_api_403_shows_friendly_message(self):
        """HTTP 403 shows authorisation error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Forbidden", status_code=403),
            ):
                result = _invoke([
                    "catalog", "search",
                    "--keyword", "orders",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "authoris" in result.output.lower() or "iam" in result.output.lower()
