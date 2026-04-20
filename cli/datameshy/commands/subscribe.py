"""CLI commands for subscription management.

Commands:
  datameshy subscribe request  — request access to a data product
  datameshy subscribe approve  — approve or deny a pending subscription
  datameshy subscribe revoke   — revoke an active subscription
  datameshy subscribe list     — list subscriptions (with optional filters)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Manage data product subscriptions (request, approve, revoke, list).")
console = Console()

# ---------------------------------------------------------------------------
# Error message mapping for API HTTP status codes
# ---------------------------------------------------------------------------
_HTTP_ERRORS: dict[int, str] = {
    403: "Not authorised — check your IAM permissions or product ownership.",
    404: "Subscription not found.",
    409: "Subscription already exists for this product/consumer pair.",
}


def _get_api_url(api_url: str | None) -> str:
    """Resolve the API Gateway base URL.

    Resolution order:
      1. ``--api-url`` flag (passed directly)
      2. ``DATAMESHY_API_URL`` environment variable
      3. ``~/.datameshy/config`` profile file (``api_url`` key)

    Raises:
        typer.Exit: If no API URL can be found.
    """
    if api_url:
        return api_url.rstrip("/")

    env_url = os.environ.get("DATAMESHY_API_URL")
    if env_url:
        return env_url.rstrip("/")

    config_path = Path.home() / ".datameshy" / "config"
    if config_path.exists():
        import configparser

        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        url = cfg.get("default", "api_url", fallback=None)
        if url:
            return url.rstrip("/")

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


# ---------------------------------------------------------------------------
# subscribe request
# ---------------------------------------------------------------------------


@app.command("request")
def subscribe_request(
    ctx: typer.Context,
    product: Annotated[
        str,
        typer.Option("--product", help="Data product ID, e.g. sales/customer_orders."),
    ],
    columns: Annotated[
        Optional[str],
        typer.Option(
            "--columns",
            help="Comma-separated list of columns to request. "
            "If omitted, all non-PII columns are requested automatically.",
        ),
    ] = None,
    justification: Annotated[
        str,
        typer.Option("--justification", help="Business justification for access."),
    ] = "",
    consumer_account_id: Annotated[
        Optional[str],
        typer.Option(
            "--consumer-account-id",
            help="AWS account ID of the consumer. Defaults to the caller's account.",
        ),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Request access to a data product.

    Submits a subscription request to the mesh governance API. The product
    owner will receive a notification and can approve or deny via
    ``datameshy subscribe approve``.

    If ``--columns`` is omitted, the CLI fetches the product catalog entry and
    automatically requests all non-PII columns.

    Example:

    \b
      datameshy subscribe request \\
        --product sales/customer_orders \\
        --columns order_id,order_date,order_total \\
        --justification "Marketing attribution model"
    """
    from datameshy.lib.aws_client import APIError, make_signed_request

    base_url = _get_api_url(api_url)

    try:
        session = _get_session_from_ctx(ctx)

        # Resolve consumer account ID
        if not consumer_account_id:
            sts = session.client("sts")
            consumer_account_id = sts.get_caller_identity()["Account"]

        # Resolve columns — fetch catalog if not provided
        requested_columns: list[str] = []
        if columns:
            requested_columns = [c.strip() for c in columns.split(",") if c.strip()]
        else:
            console.print(
                f"[dim]--columns not specified; fetching non-PII columns from catalog for {product}...[/dim]"
            )
            try:
                catalog_response = make_signed_request(
                    session=session,
                    method="GET",
                    url=f"{base_url}/catalog/{product}",
                )
                schema_columns = catalog_response.get("schema", {}).get("columns", [])
                requested_columns = [
                    col["name"]
                    for col in schema_columns
                    if not col.get("pii", False)
                ]
                if requested_columns:
                    console.print(
                        f"[dim]Auto-selected non-PII columns:[/dim] {', '.join(requested_columns)}"
                    )
                else:
                    console.print(
                        "[yellow]Warning: no non-PII columns found in catalog — "
                        "submitting request with empty column list.[/yellow]"
                    )
            except APIError as exc:
                console.print(
                    f"[yellow]Warning: could not fetch catalog for column resolution "
                    f"({exc.status_code}): {exc} — submitting with empty column list.[/yellow]"
                )

        # POST /subscriptions
        body = {
            "product_id": product,
            "consumer_account_id": consumer_account_id,
            "requested_columns": requested_columns,
            "justification": justification,
        }

        console.print(f"\n[bold]Requesting subscription to:[/bold] [cyan]{product}[/cyan]")

        response = make_signed_request(
            session=session,
            method="POST",
            url=f"{base_url}/subscriptions",
            body=body,
        )

        subscription_id = response.get("subscription_id", "-")
        status = response.get("status", "-")

        console.print(f"  [green]Subscription ID:[/green] {subscription_id}")
        console.print(f"  [green]Status:[/green]          {status}")
        console.print(
            "\n[dim]The product owner will be notified. "
            "Check progress with: [cyan]datameshy subscribe list[/cyan][/dim]"
        )

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


# ---------------------------------------------------------------------------
# subscribe approve
# ---------------------------------------------------------------------------


@app.command("approve")
def subscribe_approve(
    ctx: typer.Context,
    subscription_id: Annotated[
        str,
        typer.Option("--subscription-id", help="UUID of the subscription to approve or deny."),
    ],
    deny: Annotated[
        bool,
        typer.Option("--deny", help="Deny the subscription instead of approving it."),
    ] = False,
    comment: Annotated[
        Optional[str],
        typer.Option("--comment", help="Optional comment for the approval or denial decision."),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Approve or deny a pending subscription request.

    Only the product owner or a governance admin can call this command.

    Example:

    \b
      datameshy subscribe approve --subscription-id <uuid>
      datameshy subscribe approve --subscription-id <uuid> --comment "Approved for Q2 analysis"
      datameshy subscribe approve --subscription-id <uuid> --deny --comment "PII access not warranted"
    """
    from datameshy.lib.aws_client import make_signed_request

    base_url = _get_api_url(api_url)
    approved = not deny
    action_label = "Denying" if deny else "Approving"

    try:
        session = _get_session_from_ctx(ctx)

        body: dict = {
            "subscription_id": subscription_id,
            "approved": approved,
        }
        if comment is not None:
            body["comment"] = comment

        console.print(
            f"[bold]{action_label} subscription:[/bold] [cyan]{subscription_id}[/cyan]"
        )

        response = make_signed_request(
            session=session,
            method="POST",
            url=f"{base_url}/subscriptions/{subscription_id}/approve",
            body=body,
        )

        new_status = response.get("status", "-")
        console.print(f"  [green]New status:[/green] {new_status}")

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


# ---------------------------------------------------------------------------
# subscribe revoke
# ---------------------------------------------------------------------------


@app.command("revoke")
def subscribe_revoke(
    ctx: typer.Context,
    subscription_id: Annotated[
        str,
        typer.Option("--subscription-id", help="UUID of the subscription to revoke."),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """Revoke an active subscription.

    Removes cross-account Lake Formation grants for the subscriber. This
    action cannot be undone — the consumer will need to submit a new request.

    Example:

    \b
      datameshy subscribe revoke --subscription-id <uuid>
      datameshy subscribe revoke --subscription-id <uuid> --yes
    """
    from datameshy.lib.aws_client import make_signed_request

    base_url = _get_api_url(api_url)

    if not yes:
        confirmed = typer.confirm(
            f"Revoke subscription {subscription_id}? This will remove all data access for the consumer.",
            default=False,
        )
        if not confirmed:
            console.print("[yellow]Revoke cancelled.[/yellow]")
            return

    try:
        session = _get_session_from_ctx(ctx)

        console.print(f"[bold]Revoking subscription:[/bold] [cyan]{subscription_id}[/cyan]")

        response = make_signed_request(
            session=session,
            method="POST",
            url=f"{base_url}/subscriptions/{subscription_id}/revoke",
            body={"subscription_id": subscription_id},
        )

        new_status = response.get("status", "REVOKED")
        console.print(f"  [green]Status:[/green] {new_status}")

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)


# ---------------------------------------------------------------------------
# subscribe list
# ---------------------------------------------------------------------------


@app.command("list")
def subscribe_list(
    ctx: typer.Context,
    product: Annotated[
        Optional[str],
        typer.Option("--product", help="Filter by product ID, e.g. sales/customer_orders."),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option(
            "--status",
            help="Filter by status: PENDING, ACTIVE, REVOKED, or FAILED.",
        ),
    ] = None,
    api_url: Annotated[
        Optional[str],
        typer.Option("--api-url", help="API Gateway base URL.", envvar="DATAMESHY_API_URL"),
    ] = None,
) -> None:
    """List subscriptions visible to the caller.

    With no filters, returns all subscriptions the caller can see (as a
    consumer or as a product owner). Use ``--product`` for the producer view
    and ``--status`` to narrow by lifecycle state.

    Paginates automatically when the response includes a ``next_token``.

    Example:

    \b
      datameshy subscribe list
      datameshy subscribe list --product sales/customer_orders
      datameshy subscribe list --status PENDING
    """
    from datameshy.lib.aws_client import make_signed_request

    base_url = _get_api_url(api_url)

    try:
        session = _get_session_from_ctx(ctx)

        # Build query params (omit None values)
        query_params: dict[str, str] = {}
        if product:
            query_params["product"] = product
        if status:
            query_params["status"] = status

        all_items: list[dict] = []
        next_token: str | None = None

        # Paginate until exhausted
        while True:
            if next_token:
                query_params["next_token"] = next_token

            response = make_signed_request(
                session=session,
                method="GET",
                url=f"{base_url}/subscriptions",
                params=query_params if query_params else None,
            )

            page_items = response.get("items", response.get("subscriptions", []))
            all_items.extend(page_items)

            next_token = response.get("next_token")
            if not next_token:
                break

        if not all_items:
            console.print("[yellow]No subscriptions found matching the given filters.[/yellow]")
            return

        tbl = Table(
            title=f"Subscriptions ({len(all_items)} total)",
            show_header=True,
            header_style="bold cyan",
        )
        tbl.add_column("Subscription ID", style="dim", no_wrap=True)
        tbl.add_column("Product")
        tbl.add_column("Status")
        tbl.add_column("Requested Columns")
        tbl.add_column("Created At")

        for item in all_items:
            requested = item.get("requested_columns", [])
            cols_display = ", ".join(requested) if requested else "[dim]all[/dim]"
            tbl.add_row(
                item.get("subscription_id", "-"),
                item.get("product_id", "-"),
                item.get("status", "-"),
                cols_display,
                item.get("created_at", "-"),
            )

        console.print(tbl)
        console.print(f"[dim]Total: {len(all_items)} subscription(s)[/dim]")

    except typer.Exit:
        raise
    except Exception as exc:
        _handle_api_error(exc)
