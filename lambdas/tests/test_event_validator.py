"""
Tests for lambdas/event_validator.py

Covers:
- Valid event from registered domain passes validation
- Event from unregistered domain is rejected (domain mismatch)
- Duplicate event (same event_id) is detected and returns early
- Out-of-order event: product not yet in catalog -> not found
"""

import sys
import os

import pytest

# Add lambdas dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from event_validator import (
    validate_event_source,
    check_dedup,
    check_product_exists,
    DUPLICATE_EVENT,
    DOMAIN_MISMATCH,
    PRODUCT_NOT_FOUND,
    VALID,
)

# Constants matching conftest
DOMAINS_TABLE = "mesh-domains"
PRODUCTS_TABLE = "mesh-products"
DEDUP_TABLE = "mesh-event-dedup"
TEST_ACCOUNT_ID = "111111111111"
TEST_DOMAIN = "sales"
TEST_PRODUCT = "customer_orders"
TEST_PRODUCT_ID = "sales#customer_orders"


def _make_event(event_type, detail, account=TEST_ACCOUNT_ID, source="datameshy"):
    return {
        "version": "0",
        "id": "test-eb-id-001",
        "source": source,
        "account": account,
        "time": "2026-04-03T10:00:00Z",
        "region": "us-east-1",
        "resources": [],
        "detail-type": event_type,
        "detail": detail,
    }


class TestValidateEventSource:
    """Test event source validation: account -> domain lookup."""

    def test_valid_event_from_registered_domain(self, setup_tables, register_domain):
        """Event from a registered account with matching domain should pass."""
        event = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-001",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        result = validate_event_source(event, DOMAINS_TABLE)
        assert result["status"] == VALID
        assert result["domain"] == TEST_DOMAIN

    def test_event_from_wrong_account_rejected(self, setup_tables, register_domain):
        """Event from an unregistered account should be rejected."""
        event = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-002",
                "domain": TEST_DOMAIN,
                "product_name": TEST_PRODUCT,
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account="999999999999",
        )
        result = validate_event_source(event, DOMAINS_TABLE)
        assert result["status"] == DOMAIN_MISMATCH

    def test_event_domain_does_not_match_registered_account(self, setup_tables, register_domain):
        """Event claiming a different domain than registered for the account should be rejected."""
        event = _make_event(
            "ProductRefreshed",
            {
                "event_id": "uuid-003",
                "domain": "marketing",
                "product_name": "campaigns",
                "timestamp": "2026-04-03T10:00:00Z",
                "version": "1.0",
            },
            account=TEST_ACCOUNT_ID,
        )
        result = validate_event_source(event, DOMAINS_TABLE)
        assert result["status"] == DOMAIN_MISMATCH


class TestDedup:
    """Test event deduplication via mesh-event-dedup table."""

    def test_first_event_passes(self, setup_tables):
        """First-time event_id should pass dedup check."""
        result = check_dedup("uuid-100", DEDUP_TABLE)
        assert result["status"] == VALID

    def test_duplicate_event_detected(self, setup_tables):
        """Second event with the same event_id should be detected as duplicate."""
        result1 = check_dedup("uuid-200", DEDUP_TABLE)
        assert result1["status"] == VALID

        result2 = check_dedup("uuid-200", DEDUP_TABLE)
        assert result2["status"] == DUPLICATE_EVENT


class TestProductExists:
    """Test out-of-order resilience: product not yet in catalog."""

    def test_existing_product_passes(self, setup_tables, register_product):
        """Event referencing an existing product should pass."""
        result = check_product_exists(TEST_PRODUCT_ID, PRODUCTS_TABLE)
        assert result["status"] == VALID

    def test_missing_product_returns_not_found(self, setup_tables):
        """Event referencing a product not yet in catalog should return PRODUCT_NOT_FOUND."""
        result = check_product_exists("marketing#campaigns", PRODUCTS_TABLE)
        assert result["status"] == PRODUCT_NOT_FOUND
