"""Data Meshy CLI — entry point and global app configuration."""

from __future__ import annotations

import sys
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.panel import Panel

from datameshy import __version__
from datameshy.commands import catalog, domain, product, subscribe

console = Console()

app = typer.Typer(
    name="datameshy",
    help="Data Meshy — self-serve data mesh management on AWS.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

# Register sub-command groups
app.add_typer(catalog.app, name="catalog", help="Discover data products in the mesh catalog (search, browse, describe).")
app.add_typer(domain.app, name="domain", help="Manage mesh domains (onboard, list, status).")
app.add_typer(product.app, name="product", help="Manage data products (create, refresh, status).")
app.add_typer(subscribe.app, name="subscribe", help="Manage data product subscriptions (request, approve, revoke, list).")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"datameshy version {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    ctx: typer.Context,
    profile: Annotated[
        Optional[str],
        typer.Option("--profile", help="AWS SSO profile name.", envvar="AWS_PROFILE"),
    ] = None,
    region: Annotated[
        str,
        typer.Option("--region", help="AWS region.", envvar="AWS_DEFAULT_REGION"),
    ] = "us-east-1",
    version: Annotated[
        Optional[bool],
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = None,
) -> None:
    """Data Meshy — self-serve data mesh management on AWS.

    Global options apply to all commands. Set AWS_PROFILE and AWS_DEFAULT_REGION
    environment variables to avoid specifying them on every command.
    """
    # Store in context for sub-commands
    ctx.ensure_object(dict)
    ctx.obj["profile"] = profile
    ctx.obj["region"] = region

    # Show banner (skip for --help, --version, completion)
    if ctx.invoked_subcommand and not any(a in sys.argv for a in ["--help", "-h", "--version"]):
        _print_banner(profile=profile, region=region)


def _print_banner(profile: Optional[str], region: str) -> None:
    """Print CLI startup banner with AWS context."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

        session_kwargs: dict = {"region_name": region}
        if profile:
            session_kwargs["profile_name"] = profile

        session = boto3.Session(**session_kwargs)
        sts = session.client("sts")
        identity = sts.get_caller_identity()
        account_id = identity["Account"]
        arn = identity["Arn"]
        profile_display = profile or session.profile_name or "default"

        console.print(
            Panel(
                f"[bold cyan]Data Meshy[/bold cyan] v{__version__}\n"
                f"[dim]Profile:[/dim] [green]{profile_display}[/green]  "
                f"[dim]Account:[/dim] [green]{account_id}[/green]  "
                f"[dim]Region:[/dim] [green]{region}[/green]\n"
                f"[dim]Identity:[/dim] {arn}",
                title="[bold]Data Mesh CLI[/bold]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    except Exception:
        # Do not fail the CLI if banner cannot be shown (e.g. no creds configured)
        profile_display = profile or "default"
        console.print(
            Panel(
                f"[bold cyan]Data Meshy[/bold cyan] v{__version__}\n"
                f"[dim]Profile:[/dim] [yellow]{profile_display}[/yellow]  "
                f"[dim]Region:[/dim] [yellow]{region}[/yellow]  "
                f"[dim]AWS identity:[/dim] [yellow]unavailable[/yellow]",
                title="[bold]Data Mesh CLI[/bold]",
                border_style="yellow",
                padding=(0, 1),
            )
        )


if __name__ == "__main__":
    app()
