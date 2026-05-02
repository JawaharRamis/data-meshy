"""CLI commands for data product management.

Commands:
  datameshy product create   — provision a new data product from a spec
  datameshy product refresh  — trigger a pipeline run for a product
  datameshy product status   — show product metadata and quality info
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Manage data products (create, refresh, status).")
console = Console()

_PRODUCTS_TABLE = "mesh-products"
_LOCKS_TABLE = "mesh-pipeline-locks"


def _get_session_from_ctx(ctx: typer.Context):
    from datameshy.lib.aws_client import get_session

    obj = ctx.ensure_object(dict)
    profile = obj.get("profile")
    region = obj.get("region", "us-east-1")
    return get_session(profile=profile, region=region)


@app.command("create")
def product_create(
    ctx: typer.Context,
    spec: Annotated[
        Path,
        typer.Option("--spec", help="Path to product.yaml spec file.", exists=True, readable=True),
    ],
    event_bus_arn: Annotated[
        Optional[str],
        typer.Option("--event-bus-arn", help="Central EventBridge event bus ARN.", envvar="MESH_EVENT_BUS_ARN"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate spec and plan; do not apply.")] = False,
) -> None:
    """Create a new data product from a product.yaml spec.

    This command will:

    \b
    1. Validate the product spec against the JSON Schema
    2. Check the product does not already exist
    3. Copy pipeline templates to the domain's S3 bucket
    4. Run terraform plan for the data-product module
    5. Confirm and apply
    6. Emit a ProductCreated event

    Example:

    \b
      datameshy product create \\
        --spec examples/example-domain-repo/products/customer_orders/product.yaml
    """
    from datameshy.lib.spec_parser import SpecValidationError, parse_and_validate

    # 1. Validate spec
    console.print(f"[bold]Validating spec:[/bold] {spec}")
    try:
        parsed = parse_and_validate(str(spec))
    except SpecValidationError as exc:
        console.print(f"[red]Spec validation failed:[/red]\n{exc}")
        raise typer.Exit(code=1) from exc
    except FileNotFoundError as exc:
        console.print(f"[red]Spec file not found:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    product_name = parsed["product"]["name"]
    domain_name = parsed["product"]["domain"]
    owner = parsed["product"]["owner"]
    schema_version = parsed.get("schema_version", 1)

    console.print(f"  [green]Spec valid.[/green] Product: {domain_name}/{product_name} (schema_version={schema_version})")

    if dry_run:
        console.print("\n[yellow]--dry-run mode: spec is valid. No resources created.[/yellow]")
        return

    # 2. Check product doesn't already exist
    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")
        products_table = dynamodb.Table(_PRODUCTS_TABLE)
        existing = products_table.get_item(Key={"product_id": f"{domain_name}#{product_name}"})
        if existing.get("Item"):
            console.print(
                f"[red]Product '{domain_name}/{product_name}' already exists.[/red]\n"
                "Use 'datameshy product refresh' to run the pipeline, or update the spec and re-deploy."
            )
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[yellow]Warning: could not check existing products (DynamoDB unavailable): {exc}[/yellow]")

    # 3. Copy Glue job templates to S3 (best-effort)
    console.print("\n[bold]Copying pipeline templates to S3...[/bold]")
    try:
        s3 = session.client("s3")
        # Discover S3 bucket name following naming convention: {domain}-raw-{account_id}
        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        raw_bucket = f"{domain_name}-raw-{account_id}"
        prefix = f"pipeline-code/{product_name}/"

        templates_dir = Path(__file__).parent.parent.parent.parent / "templates" / "glue_jobs"
        if templates_dir.is_dir():
            for template in templates_dir.glob("*.py"):
                key = f"{prefix}{template.name}"
                s3.upload_file(str(template), raw_bucket, key)
                console.print(f"  Uploaded: s3://{raw_bucket}/{key}")
        else:
            console.print(f"  [dim]No templates found at {templates_dir} — skipping S3 upload.[/dim]")
    except Exception as exc:
        console.print(f"  [yellow]Warning: S3 template upload failed: {exc}[/yellow]")

    # 4–5. Terraform plan + apply
    env_dir = Path.cwd() / "infra" / "environments" / f"domain-{domain_name}"
    if env_dir.is_dir():
        from datameshy.lib.terraform_runner import TerraformError, apply, plan

        columns_json = json.dumps(
            [
                {"name": c["name"], "type": c["type"]}
                for c in parsed.get("schema", {}).get("columns", [])
            ]
        )
        tf_vars = {
            "domain": domain_name,
            "product_name": product_name,
            "owner": owner,
            "schema_columns": columns_json,
        }

        try:
            console.print("\n[bold]Running terraform plan...[/bold]")
            plan_output = plan(str(env_dir), var_overrides=tf_vars)
            lines = plan_output.splitlines()
            for line in lines[:30]:
                console.print(f"  {line}")
            if len(lines) > 30:
                console.print(f"  [dim]... ({len(lines) - 30} more lines)[/dim]")

            confirmed = typer.confirm("\nApply this plan?", default=False)
            if not confirmed:
                console.print("[yellow]Apply cancelled.[/yellow]")
                return

            apply(str(env_dir), var_overrides=tf_vars, auto_approve=True)
            console.print("[green]terraform apply succeeded.[/green]")
        except TerraformError as exc:
            console.print(f"[red]Terraform error:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    else:
        console.print(f"[dim]Terraform environment not found at {env_dir} — skipping TF apply.[/dim]")

    # 6. Emit ProductCreated event
    if event_bus_arn:
        try:
            from datameshy.lib.aws_client import put_mesh_event

            event_id = put_mesh_event(
                session=session,
                event_bus_arn=event_bus_arn,
                event_type="ProductCreated",
                payload={
                    "domain": domain_name,
                    "product_name": product_name,
                    "product_id": f"{domain_name}#{product_name}",
                    "owner": owner,
                    "schema_version": schema_version,
                },
            )
            console.print(f"[green]ProductCreated event emitted.[/green] event_id={event_id}")
        except Exception as exc:
            console.print(f"[yellow]Warning: could not emit ProductCreated event: {exc}[/yellow]")

    console.print(
        f"\n[bold green]Product '{domain_name}/{product_name}' created successfully.[/bold green]\n"
        "\nNext steps:\n"
        "  1. Implement your Glue job transforms in:\n"
        f"     [cyan]examples/{domain_name}-domain/products/{product_name}/[/cyan]\n"
        f"  2. Run the pipeline: [cyan]datameshy product refresh --domain {domain_name} --name {product_name}[/cyan]\n"
        f"  3. Check status: [cyan]datameshy product status --domain {domain_name} --name {product_name}[/cyan]"
    )


@app.command("refresh")
def product_refresh(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain", help="Domain name.")],
    name: Annotated[str, typer.Option("--name", help="Product name.")],
    timeout: Annotated[
        int,
        typer.Option("--timeout", help="Maximum seconds to wait for pipeline completion."),
    ] = 7200,
) -> None:
    """Trigger a pipeline run (raw → silver → gold) for a data product.

    This command will:

    \b
    1. Look up the product in mesh-products DynamoDB
    2. Check the pipeline is not already locked
    3. Start the Step Functions state machine
    4. Wait for completion with a live progress spinner
    5. Show quality score and rows written on success

    Example:

    \b
      datameshy product refresh --domain sales --name customer_orders
    """
    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")

        # 1. Look up product
        products_table = dynamodb.Table(_PRODUCTS_TABLE)
        product_id = f"{domain}#{name}"
        response = products_table.get_item(Key={"product_id": product_id})
        product_item = response.get("Item")

        if not product_item:
            console.print(f"[red]Product '{domain}/{name}' not found in mesh-products.[/red]")
            raise typer.Exit(code=1)

        product_status = product_item.get("status", "")
        if product_status not in ("PROVISIONED", "ACTIVE", ""):
            console.print(
                f"[red]Product is not in a refreshable state (status={product_status}).[/red]"
            )
            raise typer.Exit(code=1)

        # 2. Check pipeline lock
        locks_table = dynamodb.Table(_LOCKS_TABLE)
        lock_response = locks_table.get_item(Key={"product_id": product_id})
        lock_item = lock_response.get("Item")
        if lock_item and lock_item.get("locked"):
            execution_arn = lock_item.get("execution_arn", "unknown")
            console.print(
                f"[yellow]Pipeline is already running for '{domain}/{name}'.[/yellow]\n"
                f"Execution: {execution_arn}\n"
                "Wait for it to complete or check its status in the AWS console."
            )
            raise typer.Exit(code=0)

        # 3. Build Step Functions input
        state_machine_arn = product_item.get("state_machine_arn", "")
        if not state_machine_arn:
            console.print("[red]Product record is missing state_machine_arn.[/red]")
            raise typer.Exit(code=1)

        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        region = session.region_name or "us-east-1"

        sf_input = {
            "domain": domain,
            "product_name": name,
            "product_id": product_id,
            "raw_bucket": f"{domain}-raw-{account_id}",
            "silver_bucket": f"{domain}-silver-{account_id}",
            "gold_bucket": f"{domain}-gold-{account_id}",
            "raw_db": f"{domain}_raw",
            "silver_db": f"{domain}_silver",
            "gold_db": f"{domain}_gold",
            "table_name": name,
            "quality_ruleset_name": f"{domain}_{name}_dq",
            "glue_job_execution_role_arn": product_item.get("glue_job_execution_role_arn", ""),
            "central_event_bus_arn": product_item.get("central_event_bus_arn", ""),
            "products_table_name": _PRODUCTS_TABLE,
            "pipeline_locks_table_name": _LOCKS_TABLE,
            "audit_log_table_name": "mesh-audit-log",
        }

        # 4. Start pipeline
        from datameshy.lib.aws_client import PipelineError, start_pipeline, wait_pipeline

        console.print(f"\n[bold]Starting pipeline for {domain}/{name}...[/bold]")
        execution_arn = start_pipeline(session, state_machine_arn, sf_input)
        console.print(f"  Execution ARN: {execution_arn}")

        # 5. Wait with spinner
        console.print("\n[bold]Waiting for pipeline completion...[/bold]")
        try:
            final_status = wait_pipeline(session, execution_arn, timeout_seconds=timeout)
        except PipelineError as exc:
            console.print(f"\n[red]Pipeline {exc.status}:[/red] {exc}")
            if exc.cause:
                console.print(f"[dim]Cause:[/dim] {exc.cause}")
            console.print(
                f"\n[yellow]Check the DLQ for details:[/yellow] "
                f"https://{region}.console.aws.amazon.com/sqs/v3/home?region={region}#/queues"
            )
            raise typer.Exit(code=1) from exc

        # 5. Show results
        # Fetch updated product record for quality score
        updated = products_table.get_item(Key={"product_id": product_id}).get("Item", {})
        quality_score = updated.get("last_quality_score", "N/A")
        rows_written = updated.get("last_rows_written", "N/A")
        catalog_url = updated.get("catalog_url", f"https://console.aws.amazon.com/glue/home?region={region}#/catalog")

        console.print(f"\n[bold green]Pipeline SUCCEEDED[/bold green]")
        console.print(f"  Quality Score : {quality_score}")
        console.print(f"  Rows Written  : {rows_written}")
        console.print(f"  Catalog URL   : {catalog_url}")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error running product refresh:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("status")
def product_status(
    ctx: typer.Context,
    domain: Annotated[str, typer.Option("--domain", help="Domain name.")],
    name: Annotated[str, typer.Option("--name", help="Product name.")],
) -> None:
    """Show current metadata, quality, and subscriber info for a data product.

    Example:

    \b
      datameshy product status --domain sales --name customer_orders
    """
    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")

        product_id = f"{domain}#{name}"
        products_table = dynamodb.Table(_PRODUCTS_TABLE)
        response = products_table.get_item(Key={"product_id": product_id})
        item = response.get("Item")

        if not item:
            console.print(f"[red]Product '{domain}/{name}' not found in mesh-products.[/red]")
            raise typer.Exit(code=1)

        # Fetch subscriber count
        subscriptions_table = dynamodb.Table("mesh-subscriptions")
        try:
            subs_response = subscriptions_table.query(
                IndexName="product-index",
                KeyConditionExpression="product_id = :pid",
                ExpressionAttributeValues={":pid": product_id},
            )
            subscriber_count = subs_response.get("Count", 0)
        except Exception:
            subscriber_count = "N/A"

        tbl = Table(title=f"Product: {domain}/{name}", show_header=False, box=None)
        tbl.add_column("Key", style="bold cyan")
        tbl.add_column("Value")

        tbl.add_row("Product ID", item.get("product_id", "-"))
        tbl.add_row("Domain", item.get("domain", domain))
        tbl.add_row("Name", item.get("product_name", name))
        tbl.add_row("Owner", item.get("owner", "-"))
        tbl.add_row("Status", item.get("status", "-"))
        tbl.add_row("Schema Version", str(item.get("schema_version", "-")))
        tbl.add_row("Last Refresh", item.get("last_refresh_at", "Never"))
        tbl.add_row("Last Quality Score", str(item.get("last_quality_score", "-")))
        tbl.add_row("Last Rows Written", str(item.get("last_rows_written", "-")))
        tbl.add_row("Active Subscribers", str(subscriber_count))
        tbl.add_row("Catalog URL", item.get("catalog_url", "-"))

        console.print(tbl)

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error fetching product status:[/red] {exc}")
        raise typer.Exit(code=1) from exc
