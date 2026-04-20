"""Tests for CLI subscribe commands — request, approve, revoke, list."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from datameshy.cli import app
from datameshy.lib.aws_client import APIError

runner = CliRunner()


def _invoke(*args, **kwargs):
    """Helper to invoke CLI with the runner."""
    return runner.invoke(app, *args, **kwargs)


def _mock_session(account_id: str = "123456789012") -> MagicMock:
    """Build a minimal MagicMock boto3 session."""
    session = MagicMock()
    session.client.return_value.get_caller_identity.return_value = {"Account": account_id}
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
# subscribe request — happy path
# ---------------------------------------------------------------------------


class TestSubscribeRequest:
    """Tests for 'datameshy subscribe request'."""

    def test_request_happy_path_with_columns(self):
        """Request with explicit columns should POST to /subscriptions."""
        session = _mock_session()
        api_response = {
            "subscription_id": "sub-abc-123",
            "status": "PENDING",
        }

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "request",
                "--product", "sales/customer_orders",
                "--columns", "order_id,order_date,order_total",
                "--justification", "Marketing attribution model",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "sub-abc-123" in result.output
        assert "PENDING" in result.output

        mock_req.assert_called_once()
        call_kwargs = mock_req.call_args
        body = call_kwargs.kwargs.get("body") or call_kwargs.args[3] if len(call_kwargs.args) > 3 else None
        if body is None and call_kwargs.kwargs:
            body = call_kwargs.kwargs.get("body")
        # Verify body via the call
        _, kwargs = mock_req.call_args
        body = kwargs["body"]
        assert body["product_id"] == "sales/customer_orders"
        assert body["consumer_account_id"] == "123456789012"
        assert body["requested_columns"] == ["order_id", "order_date", "order_total"]
        assert body["justification"] == "Marketing attribution model"

    def test_request_uses_env_var_api_url(self, monkeypatch):
        """DATAMESHY_API_URL env var should be used when --api-url is absent."""
        monkeypatch.setenv("DATAMESHY_API_URL", "https://env-api.example.com/prod")
        session = _mock_session()
        api_response = {"subscription_id": "sub-xyz", "status": "PENDING"}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "subscribe", "request",
                "--product", "sales/orders",
                "--columns", "order_id",
                "--justification", "test",
            ])

        assert result.exit_code == 0, result.output
        assert "sub-xyz" in result.output

    def test_request_no_api_url_exits(self, monkeypatch):
        """Missing API URL should exit with a helpful message."""
        monkeypatch.delenv("DATAMESHY_API_URL", raising=False)
        # Also ensure config file doesn't exist by using a temp home
        result = _invoke([
            "subscribe", "request",
            "--product", "sales/orders",
            "--columns", "order_id",
            "--justification", "test",
        ])
        assert result.exit_code != 0

    def test_request_without_columns_fetches_catalog(self):
        """Omitting --columns should fetch catalog and select non-PII columns."""
        session = _mock_session()

        catalog_response = {
            "schema": {
                "columns": [
                    {"name": "order_id", "pii": False},
                    {"name": "customer_email", "pii": True},
                    {"name": "order_total", "pii": False},
                ]
            }
        }
        subscribe_response = {"subscription_id": "sub-def", "status": "PENDING"}

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=[catalog_response, subscribe_response],
            ) as mock_req:
                result = _invoke([
                    "subscribe", "request",
                    "--product", "sales/customer_orders",
                    "--justification", "test",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0, result.output
        assert "sub-def" in result.output

        # Second call is POST /subscriptions — verify non-PII columns chosen
        second_call_body = mock_req.call_args_list[1].kwargs["body"]
        assert "order_id" in second_call_body["requested_columns"]
        assert "order_total" in second_call_body["requested_columns"]
        assert "customer_email" not in second_call_body["requested_columns"]

    def test_request_without_columns_catalog_fails_gracefully(self):
        """If catalog fetch fails, should still submit with empty column list."""
        session = _mock_session()
        subscribe_response = {"subscription_id": "sub-fallback", "status": "PENDING"}

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=[
                    APIError("Not found", status_code=404),
                    subscribe_response,
                ],
            ):
                result = _invoke([
                    "subscribe", "request",
                    "--product", "sales/orders",
                    "--justification", "test",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0, result.output
        assert "sub-fallback" in result.output

    def test_request_explicit_consumer_account_id(self):
        """Explicit --consumer-account-id should be sent in the body."""
        session = _mock_session()
        api_response = {"subscription_id": "sub-acc", "status": "PENDING"}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "request",
                "--product", "sales/orders",
                "--columns", "order_id",
                "--consumer-account-id", "999999999999",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        body = mock_req.call_args.kwargs["body"]
        assert body["consumer_account_id"] == "999999999999"

    def test_request_api_403_shows_friendly_message(self):
        """HTTP 403 from API should show a friendly permission error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Forbidden", status_code=403),
            ):
                result = _invoke([
                    "subscribe", "request",
                    "--product", "sales/orders",
                    "--columns", "order_id",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "authoris" in result.output.lower() or "iam" in result.output.lower()

    def test_request_api_409_shows_conflict_message(self):
        """HTTP 409 should show a subscription-already-exists message."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Conflict", status_code=409),
            ):
                result = _invoke([
                    "subscribe", "request",
                    "--product", "sales/orders",
                    "--columns", "order_id",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "already exists" in result.output.lower()

    def test_request_api_500_shows_raw_error(self):
        """Non-mapped HTTP status should show the raw error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Internal Server Error", status_code=500),
            ):
                result = _invoke([
                    "subscribe", "request",
                    "--product", "sales/orders",
                    "--columns", "order_id",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "500" in result.output or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# subscribe approve
# ---------------------------------------------------------------------------


class TestSubscribeApprove:
    """Tests for 'datameshy subscribe approve'."""

    def test_approve_happy_path(self):
        """Approve command should POST to /subscriptions/{id}/approve."""
        session = _mock_session()
        api_response = {"status": "ACTIVE"}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "approve",
                "--subscription-id", "sub-uuid-001",
                "--comment", "Approved for Q2 analysis",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "ACTIVE" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "POST"
        assert "/subscriptions/sub-uuid-001/approve" in kwargs["url"]
        body = kwargs["body"]
        assert body["approved"] is True
        assert body["subscription_id"] == "sub-uuid-001"
        assert body["comment"] == "Approved for Q2 analysis"

    def test_deny_flag_sends_approved_false(self):
        """--deny flag should send approved=false in the body."""
        session = _mock_session()
        api_response = {"status": "DENIED"}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "approve",
                "--subscription-id", "sub-uuid-002",
                "--deny",
                "--comment", "PII access not warranted",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "DENIED" in result.output

        _, kwargs = mock_req.call_args
        body = kwargs["body"]
        assert body["approved"] is False
        assert body["comment"] == "PII access not warranted"

    def test_approve_without_comment(self):
        """Approve without --comment should not include comment key (or send None)."""
        session = _mock_session()
        api_response = {"status": "ACTIVE"}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "approve",
                "--subscription-id", "sub-no-comment",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        _, kwargs = mock_req.call_args
        body = kwargs["body"]
        # comment should be absent when not provided
        assert "comment" not in body

    def test_approve_api_404(self):
        """HTTP 404 should show subscription-not-found message."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Not found", status_code=404),
            ):
                result = _invoke([
                    "subscribe", "approve",
                    "--subscription-id", "no-such-sub",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_approve_api_403(self):
        """HTTP 403 should show authorisation error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Forbidden", status_code=403),
            ):
                result = _invoke([
                    "subscribe", "approve",
                    "--subscription-id", "sub-forbidden",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "authoris" in result.output.lower() or "iam" in result.output.lower()


# ---------------------------------------------------------------------------
# subscribe revoke
# ---------------------------------------------------------------------------


class TestSubscribeRevoke:
    """Tests for 'datameshy subscribe revoke'."""

    def test_revoke_with_yes_flag(self):
        """--yes flag should skip confirmation and POST to /revoke."""
        session = _mock_session()
        api_response = {"status": "REVOKED"}

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "revoke",
                "--subscription-id", "sub-revoke-001",
                "--yes",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "REVOKED" in result.output

        _, kwargs = mock_req.call_args
        assert "/subscriptions/sub-revoke-001/revoke" in kwargs["url"]

    def test_revoke_confirmation_declined(self):
        """Declining the confirmation prompt should cancel the revoke."""
        session = _mock_session()

        with _patch_session(session):
            with patch("typer.confirm", return_value=False):
                result = _invoke([
                    "subscribe", "revoke",
                    "--subscription-id", "sub-revoke-002",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()

    def test_revoke_confirmation_accepted(self):
        """Accepting the confirmation prompt should proceed with revoke."""
        session = _mock_session()
        api_response = {"status": "REVOKED"}

        with _patch_session(session), _patch_signed_request(api_response):
            with patch("typer.confirm", return_value=True):
                result = _invoke([
                    "subscribe", "revoke",
                    "--subscription-id", "sub-revoke-003",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0, result.output
        assert "REVOKED" in result.output

    def test_revoke_api_404(self):
        """HTTP 404 should show subscription-not-found message."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Not found", status_code=404),
            ):
                with patch("typer.confirm", return_value=True):
                    result = _invoke([
                        "subscribe", "revoke",
                        "--subscription-id", "no-such-sub",
                        "--api-url", "https://api.example.com/prod",
                    ])

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_revoke_api_403(self):
        """HTTP 403 during revoke should show authorisation error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Forbidden", status_code=403),
            ):
                result = _invoke([
                    "subscribe", "revoke",
                    "--subscription-id", "sub-forbidden",
                    "--yes",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "authoris" in result.output.lower() or "iam" in result.output.lower()


# ---------------------------------------------------------------------------
# subscribe list
# ---------------------------------------------------------------------------


class TestSubscribeList:
    """Tests for 'datameshy subscribe list'."""

    def test_list_all_subscriptions(self):
        """List with no filters should call GET /subscriptions and render a table."""
        session = _mock_session()
        api_response = {
            "items": [
                {
                    "subscription_id": "sub-001",
                    "product_id": "sales/customer_orders",
                    "status": "ACTIVE",
                    "requested_columns": ["order_id", "order_date"],
                    "created_at": "2026-04-01T10:00:00Z",
                },
                {
                    "subscription_id": "sub-002",
                    "product_id": "finance/invoices",
                    "status": "PENDING",
                    "requested_columns": [],
                    "created_at": "2026-04-02T12:00:00Z",
                },
            ]
        }

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "list",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "sub-001" in result.output
        assert "ACTIVE" in result.output
        # Rich may truncate long product IDs with ellipsis — check prefix only
        assert "sales/custom" in result.output
        assert "PENDING" in result.output

        _, kwargs = mock_req.call_args
        assert kwargs["method"] == "GET"
        assert "/subscriptions" in kwargs["url"]

    def test_list_with_product_filter(self):
        """--product filter should be passed as a query param."""
        session = _mock_session()
        api_response = {
            "items": [
                {
                    "subscription_id": "sub-003",
                    "product_id": "sales/customer_orders",
                    "status": "ACTIVE",
                    "requested_columns": ["order_id"],
                    "created_at": "2026-04-01T10:00:00Z",
                }
            ]
        }

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "list",
                "--product", "sales/customer_orders",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "sub-003" in result.output

        _, kwargs = mock_req.call_args
        params = kwargs.get("params", {})
        assert params.get("product") == "sales/customer_orders"

    def test_list_with_status_filter(self):
        """--status filter should be passed as a query param."""
        session = _mock_session()
        api_response = {
            "items": [
                {
                    "subscription_id": "sub-004",
                    "product_id": "finance/invoices",
                    "status": "PENDING",
                    "requested_columns": [],
                    "created_at": "2026-04-03T08:00:00Z",
                }
            ]
        }

        with _patch_session(session), _patch_signed_request(api_response) as mock_req:
            result = _invoke([
                "subscribe", "list",
                "--status", "PENDING",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "PENDING" in result.output

        _, kwargs = mock_req.call_args
        params = kwargs.get("params", {})
        assert params.get("status") == "PENDING"

    def test_list_empty_result(self):
        """Empty list should print a helpful message and exit cleanly."""
        session = _mock_session()
        api_response = {"items": []}

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "subscribe", "list",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "no subscriptions" in result.output.lower()

    def test_list_paginates_next_token(self):
        """Should follow next_token until exhausted."""
        session = _mock_session()

        page1 = {
            "items": [
                {
                    "subscription_id": "sub-p1",
                    "product_id": "sales/orders",
                    "status": "ACTIVE",
                    "requested_columns": ["id"],
                    "created_at": "2026-04-01T00:00:00Z",
                }
            ],
            "next_token": "token-abc",
        }
        page2 = {
            "items": [
                {
                    "subscription_id": "sub-p2",
                    "product_id": "sales/orders",
                    "status": "PENDING",
                    "requested_columns": [],
                    "created_at": "2026-04-02T00:00:00Z",
                }
            ],
        }

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=[page1, page2],
            ) as mock_req:
                result = _invoke([
                    "subscribe", "list",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code == 0, result.output
        assert "sub-p1" in result.output
        assert "sub-p2" in result.output
        assert mock_req.call_count == 2

    def test_list_api_403(self):
        """HTTP 403 from list should show authorisation error."""
        session = _mock_session()

        with _patch_session(session):
            with patch(
                "datameshy.lib.aws_client.make_signed_request",
                side_effect=APIError("Forbidden", status_code=403),
            ):
                result = _invoke([
                    "subscribe", "list",
                    "--api-url", "https://api.example.com/prod",
                ])

        assert result.exit_code != 0
        assert "authoris" in result.output.lower() or "iam" in result.output.lower()

    def test_list_subscriptions_key_fallback(self):
        """Response with 'subscriptions' key (not 'items') should still render."""
        session = _mock_session()
        api_response = {
            "subscriptions": [
                {
                    "subscription_id": "sub-alt",
                    "product_id": "ops/logs",
                    "status": "ACTIVE",
                    "requested_columns": ["ts", "msg"],
                    "created_at": "2026-04-04T00:00:00Z",
                }
            ]
        }

        with _patch_session(session), _patch_signed_request(api_response):
            result = _invoke([
                "subscribe", "list",
                "--api-url", "https://api.example.com/prod",
            ])

        assert result.exit_code == 0, result.output
        assert "sub-alt" in result.output


# ---------------------------------------------------------------------------
# subscribe --help
# ---------------------------------------------------------------------------


class TestSubscribeHelp:
    """Tests that the subscribe command group registers correctly."""

    def test_subscribe_help_shows_subcommands(self):
        """datameshy subscribe --help should list all four subcommands."""
        result = _invoke(["subscribe", "--help"])
        assert result.exit_code == 0
        for cmd in ("request", "approve", "revoke", "list"):
            assert cmd in result.output

    def test_subscribe_request_help(self):
        """subscribe request --help should exit cleanly."""
        result = _invoke(["subscribe", "request", "--help"])
        assert result.exit_code == 0

    def test_subscribe_approve_help(self):
        """subscribe approve --help should exit cleanly."""
        result = _invoke(["subscribe", "approve", "--help"])
        assert result.exit_code == 0

    def test_subscribe_revoke_help(self):
        """subscribe revoke --help should exit cleanly."""
        result = _invoke(["subscribe", "revoke", "--help"])
        assert result.exit_code == 0

    def test_subscribe_list_help(self):
        """subscribe list --help should exit cleanly."""
        result = _invoke(["subscribe", "list", "--help"])
        assert result.exit_code == 0
