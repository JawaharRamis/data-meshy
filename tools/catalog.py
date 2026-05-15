"""Platform-internal catalog query tool. Not for domain teams. Run with: python tools/catalog.py --help

Queries the DynamoDB mesh-products table directly using local AWS credentials
(AWS SSO profile or environment variables).  No API Gateway, no SigV4 signing
ceremony beyond what boto3 handles automatically.

Usage examples:
  python tools/catalog.py search --domain sales
  python tools/catalog.py search --keyword orders
  python tools/catalog.py search --tag ecommerce
  python tools/catalog.py browse
  python tools/catalog.py browse --domain sales
  python tools/catalog.py describe sales customer_orders
  python tools/catalog.py --profile central-admin --region us-east-1 search --domain sales
"""

from __future__ import annotations

import argparse
import re
import sys

import boto3

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEGMENT_RE = re.compile(r"^[a-z0-9_-]{1,128}$")

_STATUS_SYMBOLS: dict[str, str] = {
    "ACTIVE": "[ACTIVE]",
    "PROVISIONED": "[PROVISIONED]",
    "DEPRECATED": "[DEPRECATED]",
    "RETIRED": "[RETIRED]",
}


# ---------------------------------------------------------------------------
# DynamoDB helpers
# ---------------------------------------------------------------------------


def _get_table(profile: str | None, region: str, table_name: str):
    """Return a DynamoDB Table resource using the given profile and region."""
    session = boto3.Session(profile_name=profile, region_name=region)
    ddb = session.resource("dynamodb")
    return ddb.Table(table_name)


def _scan_table(table, filter_kwargs: dict | None = None) -> list[dict]:
    """Scan the table, following pagination automatically."""
    kwargs: dict = filter_kwargs or {}
    items: list[dict] = []
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key
    return items


def _query_by_domain(table, domain: str) -> list[dict]:
    """Query products by domain using the DynamoDB scan with FilterExpression.

    The partition key is ``domain#product_name``, so querying by domain requires
    a scan with a filter.  For a production environment with a domain GSI this
    would use ``query()`` instead.
    """
    from boto3.dynamodb.conditions import Attr

    return _scan_table(table, {"FilterExpression": Attr("domain").eq(domain)})


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _status_label(status: str) -> str:
    return _STATUS_SYMBOLS.get(status, f"[{status}]")


def _print_separator(char: str = "-", width: int = 72) -> None:
    print(char * width)


def _print_product_row(item: dict) -> None:
    """Print a single product as a compact one-line summary."""
    domain = item.get("domain", "-")
    name = item.get("product_name", "-")
    status = _status_label(item.get("status", "-"))
    classification = item.get("classification", "-")
    quality = item.get("quality_score")
    quality_str = f"{float(quality):.1f}" if quality is not None else "-"
    description = (item.get("description") or "")[:60]
    print(f"  {domain}/{name}  {status}  class={classification}  quality={quality_str}  {description}")


def _print_search_results(items: list[dict], title: str) -> None:
    """Print a list of products as a plain-text table."""
    _print_separator("=")
    print(f"  {title}  ({len(items)} product(s))")
    _print_separator("=")
    if not items:
        print("  No products found matching the given criteria.")
        return
    for item in items:
        _print_product_row(item)
    _print_separator()


def _print_product_detail(product: dict) -> None:
    """Print full product metadata."""
    name = product.get("product_name", "-")
    domain = product.get("domain", "-")
    status = _status_label(product.get("status", "-"))
    owner = product.get("owner", "-")
    description = product.get("description", "-")
    classification = product.get("classification", "-")
    tags = product.get("tags", [])
    quality_score = product.get("quality_score")
    subscriber_count = product.get("subscriber_count", 0)
    last_refreshed = product.get("last_refreshed_at", "-")
    sla = product.get("sla", {})
    schema = product.get("schema", {})
    columns = schema.get("columns", [])

    quality_str = f"{float(quality_score):.1f}" if quality_score is not None else "N/A"

    _print_separator("=")
    print(f"  Data Product: {domain}/{name}")
    _print_separator("=")
    print(f"  Status         : {status}")
    print(f"  Owner          : {owner}")
    print(f"  Classification : {classification}")
    print(f"  Quality Score  : {quality_str}")
    print(f"  Subscribers    : {subscriber_count}")
    print(f"  Last Refreshed : {last_refreshed}")

    if description and description != "-":
        _print_separator()
        print(f"  Description")
        print(f"    {description}")

    if sla:
        _print_separator()
        print("  SLA")
        for k, v in sla.items():
            print(f"    {k}: {v}")

    if tags:
        _print_separator()
        print("  Tags")
        print(f"    {', '.join(str(t) for t in tags)}")

    if columns:
        _print_separator()
        print("  Schema")
        print(f"    {'Column':<30} {'Type':<20} PII")
        _print_separator("-", 60)
        for col in columns:
            pii_flag = "YES" if col.get("pii") else "no"
            print(f"    {col.get('name', '-'):<30} {col.get('type', '-'):<20} {pii_flag}")

    _print_separator()


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_search(args: argparse.Namespace) -> int:
    """Implement: catalog.py search [--domain D] [--keyword K] [--tag T]"""
    filters = [v for v in (args.domain, args.keyword, args.tag) if v is not None]
    if len(filters) == 0:
        print("Error: provide one of --domain, --keyword, or --tag.", file=sys.stderr)
        return 1
    if len(filters) > 1:
        print("Error: provide exactly one search filter.", file=sys.stderr)
        return 1

    table = _get_table(args.profile, args.region, args.table)

    from boto3.dynamodb.conditions import Attr

    if args.domain is not None:
        items = _query_by_domain(table, args.domain)
        title = f"Domain: {args.domain}"
    elif args.keyword is not None:
        kw = args.keyword.lower()
        all_items = _scan_table(table)
        items = [
            i for i in all_items
            if kw in (i.get("product_name") or "").lower()
            or kw in (i.get("description") or "").lower()
            or any(kw in str(t).lower() for t in i.get("tags", []))
        ]
        title = f'Keyword search: "{args.keyword}"'
    else:
        tag_val = args.tag
        all_items = _scan_table(table)
        items = [i for i in all_items if tag_val in i.get("tags", [])]
        title = f"Tag: {tag_val}"

    _print_search_results(items, title)
    return 0


def cmd_browse(args: argparse.Namespace) -> int:
    """Implement: catalog.py browse [--domain D]"""
    table = _get_table(args.profile, args.region, args.table)

    if args.domain:
        items = _query_by_domain(table, args.domain)
        _print_search_results(items, f"Domain: {args.domain}")
        return 0

    all_items = _scan_table(table)
    if not all_items:
        print("No domains or products found in the catalog.")
        return 0

    # Group by domain
    domains: dict[str, list[dict]] = {}
    for item in all_items:
        dom = item.get("domain", "unknown")
        domains.setdefault(dom, []).append(item)

    total = sum(len(v) for v in domains.values())
    _print_separator("=")
    print(f"  Catalog Browse — {len(domains)} domain(s), {total} product(s)")
    _print_separator("=")
    for dom_name in sorted(domains.keys()):
        _print_search_results(domains[dom_name], f"Domain: {dom_name}")

    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    """Implement: catalog.py describe <domain> <product>"""
    domain = args.domain_name
    product = args.product_name

    if not _SEGMENT_RE.fullmatch(domain):
        print(
            f"Error: invalid domain '{domain}'. Must match ^[a-z0-9_-]{{1,128}}$.",
            file=sys.stderr,
        )
        return 1
    if not _SEGMENT_RE.fullmatch(product):
        print(
            f"Error: invalid product '{product}'. Must match ^[a-z0-9_-]{{1,128}}$.",
            file=sys.stderr,
        )
        return 1

    table = _get_table(args.profile, args.region, args.table)
    pk = f"{domain}#{product}"
    response = table.get_item(Key={"domain#product_name": pk})
    item = response.get("Item")

    if item is None:
        print(f"Error: product '{domain}/{product}' not found.", file=sys.stderr)
        return 1

    _print_product_detail(item)
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="catalog.py",
        description=(
            "Platform-internal catalog query tool. "
            "Not for domain teams. "
            "Queries the DynamoDB mesh-products table directly."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Global flags
    parser.add_argument(
        "--profile",
        metavar="AWS_PROFILE",
        default=None,
        help="AWS named profile to use (e.g. central-admin). Defaults to the environment default.",
    )
    parser.add_argument(
        "--region",
        metavar="AWS_REGION",
        default="us-east-1",
        help="AWS region. Default: us-east-1.",
    )
    parser.add_argument(
        "--table",
        metavar="PRODUCTS_TABLE",
        default="mesh-products",
        help="DynamoDB table name. Default: mesh-products.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # --- search ---
    search_p = subparsers.add_parser(
        "search",
        help="Search products by domain, keyword, or tag.",
        description="Search the data product catalog. Provide exactly one filter.",
    )
    search_p.add_argument("--domain", "-d", default=None, help="Filter by domain name.")
    search_p.add_argument(
        "--keyword", "-k", default=None, help="Full-text keyword (name, description, tags)."
    )
    search_p.add_argument("--tag", "-t", default=None, help="Filter by tag value.")
    search_p.set_defaults(func=cmd_search)

    # --- browse ---
    browse_p = subparsers.add_parser(
        "browse",
        help="List all products grouped by domain.",
        description="Browse all data products. Optionally filter to a single domain.",
    )
    browse_p.add_argument(
        "--domain", "-d", default=None, help="Limit to a single domain."
    )
    browse_p.set_defaults(func=cmd_browse)

    # --- describe ---
    describe_p = subparsers.add_parser(
        "describe",
        help="Show full metadata for a specific product.",
        description="Show full metadata for a data product: schema, SLA, tags, quality score.",
    )
    describe_p.add_argument("domain_name", metavar="<domain>", help="Domain name.")
    describe_p.add_argument("product_name", metavar="<product>", help="Product name.")
    describe_p.set_defaults(func=cmd_describe)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
