"""CLI commands for catalog discovery.

Commands:
  datameshy catalog search    — search products by keyword, domain, tag, or classification
  datameshy catalog browse    — list all products grouped by domain
  datameshy catalog describe  — show full metadata for a specific product
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(help="Discover data products in the mesh catalog (search, browse, describe).")
console = Console()

# ---------------------------------------------------------------------------
# Status colour mapping
# ---------------------------------------------------------------------------
_STATUS_COLOURS: dict[str, str] = {
    "ACTIVE": "green",
    "PROVISIONED": "cyan",
    "DEPRECATED": "yellow",
    "RETIRED": "red",
}

# ---------------------------------------------------------------------------
# Error message mapping for API HTTP status codes
# ---------------------------------------------------------------------------
_HTTP_ERRORS: dict[int, str] = {
    403: "Not authorised — check your IAM permissions.",
    404: "Product not found.",
    500: "Internal server error — check Lambda logs.",
}


# ---------------------------------------------------------------------------
# Helpers shared across subcommands
# ---------------------------------------------------------------------------


_API_URL_PATTERN = re.compile(
    r"^https://[a-zA-Z0-9\-]+\.execute-api\.[a-zA-Z0-9\-]+\.amazonaws\.com"
)


def _validate_api_url(url: str) -> str:
    """Raise BadParameter if *url* does not look like an API Gateway URL."""
    if not _API_URL_PATTERN.match(url):
        raise typer.BadParameter(
            f"--api-url must match https://<id>.execute-api.<region>.amazonaws.com/... "
            f"Got: '{url}'"
        )
    return url


def _get_api_url(api_url: str | None) -> str:
    """Resolve the API Gateway base URL.

    Resolution order:
      1. ``--api-url`` flag (passed directly)
      2. ``DATAMESHY_API_URL`` environment variable
      3. ``~/.datameshy/config`` profile file (``api_url`` key)
    """
    if api_url:
        resolved = api_url.rstrip("/")
        _validate_api_url(resolved)
        return resolved

    env_url = os.environ.get("DATAMESHY_API_URL")
    if env_url:
        resolved = env_url.rstrip("/")
        _validate_api_url(resolved)
        return resolved

    config_path = Path.home() / ".datameshy" / "config"
    if config_path.exists():
        import configparser

        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        url = cfg.get("default", "api_url", fallback=None)
        if url:
            resolved = url.rstrip("/")
            _validate_api_url(resolved)
            return resolved

    console.print(
        "[red]API URL not configured.[/red]\n"
        "Set [cyan]DATAMESHY_API_URL[/cyan] or pass [cyan]--api-url[/cyan].\n"
        "Example: export DATAMESHY_API_URL=https://<id>.execute-api.<region>.amazonaws.com/prod"
    )
    raise typer.Exit(code=1)


def _get_session_from_ctx(ctx: typer.Context):
    """Retrieve or create a boto3 session from the Typer context."""
    from datameshy.lib.aws_client import get_session

    obj = ctx.ensure_object(dict)
    profile = obj.get("profile")
    region = obj.get("region", "us-east-1")
    return get_session(profile=profile, region=region)


def _handle_api_error(exc: Exception) -> None:
    """Print a user-friendly error message for APIError and re-raise Exit."""
    from datameshy.lib.aws_client import APIError

    if isinstance(exc, APIError):
        friendly = _HTTP_ERRORS.get(exc.status_code)
        if friendly:
            console.print(f"[red]Error:[/red] {friendly}")
        else:
            console.print(f"[red]API error ({exc.status_code}):[/red] {exc}")
    else:
        console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=1)


def _status_style(status: str) -> str:
    colour = _STATUS_COLOURS.get(status, "white")
    return f"[{colour}]{status}[/{colour}]"


def _render_search_results(items: list[dict], title: str = "Search Results") -> None:
    """Render a list of products as a Rich table."""
    if not items:
        console.print("[yellow]No products found matching the given criteria.[/yellow]")
        return

    tbl = Table(
        title=f"{title} ({len(items)} product(s))",
        show_header=True,
        header_style="bold cyan",
    )
    tbl.add_column("Domain", style="dim")
    tbl.add_column("Product")
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Classification")
    tbl.add_column("Quality")
    tbl.add_column("Description")

    for item in items:
        status = item.get("status", "-")
        quality = item.get("quality_score")
        quality_str = f"{float(quality):.1f}" if quality is not None else "-"
        tbl.add_row(
            item.get("domain", "-"),
            item.get("product_name", "-"),
            _status_style(status),
            item.get("classification", "-"),
            quality_str,
            (item.get("description") or "-")[:60],
        )

    console.print(tbl)


# ---------------------------------------------------------------------------
# catalog search
# ---------------------------------------------------------------------------


@app.command("search")
def catalog_search(
    ctx: typer.Context,
    keyword: Annotated[
        Optional[str],
        typer.Option("--keyword", help="Full-text keyword to match on name, description, tags."),
    ] = None,
    domain: Annotated[
        Optional[str],
        typer.Option("--domain", help="Filter by domain name (uses domain GSI)."),
    ] = None,
    tag: Annotated[
        Optional[str],
        typer.Option("--tag", help="Filter by tag value, e.g. env=prod or ecommerce (uses tag GSI)."),
    ] = None,
    classification: Annotated[
        Optional[str],
        typer.Option(
            "--classification",
            help="Filter by classification level, e.g. internal, confidential (uses classification GSI).",
        ),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Search the data product catalog.

    Exactly one filter must be provided. Results include products of all
    statuses (ACTIVE, DEPRECATED, RETIRED, PROVISIONED).

    Example:

    \b
      datameshy catalog search --keyword orders
      datameshy catalog search --domain sales
      datameshy catalog search --tag ecommerce
      datameshy catalog search --classification internal
    """
    from datameshy.lib.aws_client import make_signed_request

    base_url = _get_api_url(api_url)

    # Validate filters
    provided = [v for v in (keyword, domain, tag, classification) if v is not None]
    if len(provided) == 0:
        console.print("[red]Error:[/red] Provide one of --keyword, --domain, --tag, --classification.")
        raise typer.Exit(code=1)
    if len(provided) > 1:
        console.print("[red]Error:[/red] Provide exactly one search filter.")
        raise typer.Exit(code=1)

    try:
        session = _get_session_from_ctx(ctx)

        params: dict[str, str] = {}
        if keyword is not None:
            params["keyword"] = keyword
            title = f'Keyword search: "{keyword}"'
        elif domain is not None:
            params["domain"] = domain
            title = f"Domain: {domain}"
        elif tag is not None:
            params["tag"] = tag
            title = f"Tag: {tag}"
        else:
            params["classification"] = classification  # type: ignore[assignment]
            title = f"Classification: {classification}"

        response = make_signed_request(
            session=session,
            method="GET",
            url=f"{base_url}/catalog/search",
            params=params,
        )

        items = response.get("items", [])
        _render_search_results(items, title=title)

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


# ---------------------------------------------------------------------------
# catalog browse
# ---------------------------------------------------------------------------


@app.command("browse")
def catalog_browse(
    ctx: typer.Context,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Browse all data products grouped by domain.

    Displays products of all statuses. Paginates automatically.

    Example:

    \b
      datameshy catalog browse
    """
    from datameshy.lib.aws_client import make_signed_request

    base_url = _get_api_url(api_url)

    try:
        session = _get_session_from_ctx(ctx)

        all_domains: dict[str, list[dict]] = {}
        params: dict[str, str] = {}
        next_token: str | None = None

        while True:
            if next_token:
                params["next_token"] = next_token

            response = make_signed_request(
                session=session,
                method="GET",
                url=f"{base_url}/catalog/browse",
                params=params if params else None,
            )

            page_domains: dict[str, list] = response.get("domains", {})
            for dom, products in page_domains.items():
                all_domains.setdefault(dom, []).extend(products)

            next_token = response.get("next_token")
            if not next_token:
                break

        if not all_domains:
            console.print("[yellow]No domains or products found in the catalog.[/yellow]")
            return

        total = sum(len(v) for v in all_domains.values())
        console.print(
            Panel(
                f"[bold cyan]Catalog Browse[/bold cyan] — "
                f"[dim]{len(all_domains)} domain(s), {total} product(s)[/dim]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

        for dom_name in sorted(all_domains.keys()):
            products = all_domains[dom_name]
            _render_search_results(products, title=f"Domain: {dom_name}")

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


# ---------------------------------------------------------------------------
# catalog describe
# ---------------------------------------------------------------------------


@app.command("describe")
def catalog_describe(
    ctx: typer.Context,
    product_path: Annotated[
        str,
        typer.Argument(
            metavar="DOMAIN/PRODUCT",
            help="Product identifier in <domain>/<product_name> format, e.g. sales/customer_orders.",
        ),
    ],
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Show full metadata for a data product.

    Displays schema, quality score, SLA, tags, subscriber count, and status.
    Works for products of all statuses (ACTIVE, DEPRECATED, RETIRED).

    Example:

    \b
      datameshy catalog describe sales/customer_orders
    """
    from datameshy.lib.aws_client import make_signed_request

    # Validate format
    if "/" not in product_path:
        console.print(
            f"[red]Error:[/red] Product must be in [cyan]<domain>/<product_name>[/cyan] format. "
            f"Got: '{product_path}'"
        )
        raise typer.Exit(code=1)

    # CRITICAL: validate each segment to prevent path traversal
    _SEGMENT_RE = re.compile(r"^[a-z0-9_-]{1,128}$")
    parts = product_path.split("/")
    if len(parts) != 2:
        raise typer.BadParameter(
            f"Product path must be exactly <domain>/<product_name>. Got: '{product_path}'"
        )
    domain_seg, product_seg = parts
    for seg_name, seg_val in (("domain", domain_seg), ("product_name", product_seg)):
        if not _SEGMENT_RE.fullmatch(seg_val):
            raise typer.BadParameter(
                f"Invalid {seg_name} segment '{seg_val}'. "
                "Must match ^[a-z0-9_-]{1,128}$."
            )

    base_url = _get_api_url(api_url)

    try:
        session = _get_session_from_ctx(ctx)

        product = make_signed_request(
            session=session,
            method="GET",
            url=f"{base_url}/catalog/{domain_seg}/{product_seg}",
        )

        _render_product_detail(product)

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


def _render_product_detail(product: dict) -> None:
    """Render full product metadata as a Rich panel with sections."""
    name = product.get("product_name", "-")
    domain = product.get("domain", "-")
    status = product.get("status", "-")
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

    status_styled = _status_style(status)
    quality_str = f"{float(quality_score):.1f}" if quality_score is not None else "N/A"

    # Header panel
    console.print(
        Panel(
            f"[bold cyan]{domain}/{name}[/bold cyan]\n"
            f"[dim]Status:[/dim]  {status_styled}     "
            f"[dim]Owner:[/dim] {owner}\n"
            f"[dim]Classification:[/dim] {classification}     "
            f"[dim]Quality Score:[/dim] [bold]{quality_str}[/bold]\n"
            f"[dim]Subscribers:[/dim] {subscriber_count}     "
            f"[dim]Last Refreshed:[/dim] {last_refreshed}",
            title=f"[bold]Data Product: {name}[/bold]",
            border_style="cyan",
            padding=(0, 1),
        )
    )

    # Description
    if description and description != "-":
        console.print(f"\n[bold]Description[/bold]\n{description}\n")

    # SLA
    if sla:
        console.print("[bold]SLA[/bold]")
        for k, v in sla.items():
            console.print(f"  [dim]{k}:[/dim] {v}")
        console.print()

    # Tags
    if tags:
        tag_str = ", ".join(str(t) for t in tags)
        console.print(f"[bold]Tags[/bold]\n  {tag_str}\n")

    # Schema
    if columns:
        console.print("[bold]Schema[/bold]")
        tbl = Table(show_header=True, header_style="bold dim")
        tbl.add_column("Column")
        tbl.add_column("Type")
        tbl.add_column("PII")
        for col in columns:
            pii_flag = "[red]YES[/red]" if col.get("pii") else "[green]no[/green]"
            tbl.add_row(col.get("name", "-"), col.get("type", "-"), pii_flag)
        console.print(tbl)
