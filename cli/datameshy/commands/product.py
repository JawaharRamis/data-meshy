"""CLI commands for data product management.

Commands:
  datameshy product create    — provision a new data product from a spec
  datameshy product refresh   — trigger a pipeline run for a product
  datameshy product status    — show product metadata and quality info
  datameshy product deprecate — mark a product DEPRECATED with a sunset date
  datameshy product rollback  — restore gold table to a prior Iceberg snapshot
  datameshy product import    — register an existing Glue Iceberg table as a mesh product
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Manage data products (create, refresh, status, deprecate, rollback, import).")
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
        if item.get("sunset_date"):
            tbl.add_row("Sunset Date", item["sunset_date"])
        if item.get("retired_at"):
            tbl.add_row("Retired At", item["retired_at"])
        if item.get("import_source"):
            tbl.add_row("Import Source", item["import_source"])
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


@app.command("deprecate")
def product_deprecate(
    ctx: typer.Context,
    product_ref: Annotated[str, typer.Argument(help="Product reference as <domain>/<product>.")],
    sunset_days: Annotated[
        int,
        typer.Option("--sunset-days", help="Number of days until the product is retired."),
    ],
    event_bus_arn: Annotated[
        Optional[str],
        typer.Option("--event-bus-arn", help="Central EventBridge event bus ARN.", envvar="MESH_EVENT_BUS_ARN"),
    ] = None,
    retirement_lambda_arn: Annotated[
        Optional[str],
        typer.Option("--retirement-lambda-arn", help="ARN of the retirement Lambda.", envvar="MESH_RETIREMENT_LAMBDA_ARN"),
    ] = None,
    scheduler_role_arn: Annotated[
        Optional[str],
        typer.Option("--scheduler-role-arn", help="IAM role ARN for EventBridge Scheduler.", envvar="MESH_SCHEDULER_ROLE_ARN"),
    ] = None,
) -> None:
    """Mark a data product as DEPRECATED with a computed sunset date.

    This command will:

    \b
    1. Fetch the product from mesh-products — rejects if status is not ACTIVE
    2. Compute sunset_date = today + sunset_days
    3. Write status=DEPRECATED, sunset_date, sunset_days to DynamoDB
    4. Emit ProductDeprecated event with breaking=true and sunset_date
    5. Create an EventBridge Scheduler one-shot rule at sunset_date targeting the retirement Lambda

    Example:

    \b
      datameshy product deprecate sales/customer_orders --sunset-days 90
    """
    if "/" not in product_ref:
        console.print("[red]product_ref must be in the format <domain>/<product>.[/red]")
        raise typer.Exit(code=1)

    domain, product_name = product_ref.split("/", 1)
    product_id = f"{domain}#{product_name}"

    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")
        products_table = dynamodb.Table(_PRODUCTS_TABLE)

        # 1. Fetch product
        response = products_table.get_item(Key={"product_id": product_id})
        item = response.get("Item")
        if not item:
            console.print(f"[red]Product '{domain}/{product_name}' not found in mesh-products.[/red]")
            raise typer.Exit(code=1)

        current_status = item.get("status", "")
        if current_status == "DEPRECATED":
            console.print(
                f"[red]Product '{domain}/{product_name}' is already DEPRECATED.[/red]\n"
                f"Sunset date: {item.get('sunset_date', 'unknown')}"
            )
            raise typer.Exit(code=1)
        if current_status == "RETIRED":
            console.print(
                f"[red]Cannot deprecate '{domain}/{product_name}': product is already RETIRED.[/red]"
            )
            raise typer.Exit(code=1)
        if current_status not in ("ACTIVE", "PROVISIONED"):
            console.print(
                f"[red]Product '{domain}/{product_name}' is not in a depreciable state (status={current_status}).[/red]"
            )
            raise typer.Exit(code=1)

        # 2. Compute sunset_date
        sunset_date = (datetime.now(timezone.utc) + timedelta(days=sunset_days)).strftime("%Y-%m-%d")

        # 3. Update DynamoDB
        products_table.update_item(
            Key={"product_id": product_id},
            UpdateExpression=(
                "SET #s = :deprecated, sunset_date = :sd, sunset_days = :sdays, deprecated_at = :now"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":deprecated": "DEPRECATED",
                ":sd": sunset_date,
                ":sdays": sunset_days,
                ":now": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        console.print(f"[green]Product '{domain}/{product_name}' marked DEPRECATED.[/green]")
        console.print(f"  Sunset date: {sunset_date} ({sunset_days} days from today)")

        # 4. Emit ProductDeprecated event
        if event_bus_arn:
            try:
                from datameshy.lib.aws_client import put_mesh_event

                put_mesh_event(
                    session=session,
                    event_bus_arn=event_bus_arn,
                    event_type="ProductDeprecated",
                    payload={
                        "domain": domain,
                        "product_name": product_name,
                        "product_id": product_id,
                        "sunset_date": sunset_date,
                        "sunset_days": sunset_days,
                        "breaking": True,
                    },
                )
                console.print("[green]ProductDeprecated event emitted.[/green]")
            except Exception as exc:
                console.print(f"[yellow]Warning: could not emit ProductDeprecated event: {exc}[/yellow]")

        # 5. Create EventBridge Scheduler one-shot rule at sunset_date
        if retirement_lambda_arn:
            try:
                import boto3
                import json as _json

                scheduler = boto3.client("scheduler", region_name=session.region_name or "us-east-1")
                schedule_name = f"retire-{domain}-{product_name}"
                # AT expression: at(yyyy-MM-ddTHH:mm:ss) in UTC
                at_expr = f"at({sunset_date}T00:00:00)"

                sched_kwargs = dict(
                    Name=schedule_name,
                    ScheduleExpression=at_expr,
                    ScheduleExpressionTimezone="UTC",
                    FlexibleTimeWindow={"Mode": "OFF"},
                    Target={
                        "Arn": retirement_lambda_arn,
                        "Input": _json.dumps({"product_id": product_id, "domain": domain, "product_name": product_name}),
                        **({"RoleArn": scheduler_role_arn} if scheduler_role_arn else {}),
                    },
                    ActionAfterCompletion="DELETE",
                )
                scheduler.create_schedule(**sched_kwargs)
                console.print(f"[green]Retirement scheduler rule created: {schedule_name}[/green]")
            except Exception as exc:
                console.print(f"[yellow]Warning: could not create retirement scheduler rule: {exc}[/yellow]")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error deprecating product:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("rollback")
def product_rollback(
    ctx: typer.Context,
    product_ref: Annotated[str, typer.Argument(help="Product reference as <domain>/<product>.")],
    to_snapshot: Annotated[
        Optional[int],
        typer.Option("--to-snapshot", help="Iceberg snapshot ID to roll back to."),
    ] = None,
    list_snapshots: Annotated[
        bool,
        typer.Option("--list-snapshots", help="List available Iceberg snapshots instead of rolling back."),
    ] = False,
    glue_job_name: Annotated[
        Optional[str],
        typer.Option("--glue-job-name", help="Name of the Glue rollback job.", envvar="MESH_ROLLBACK_GLUE_JOB"),
    ] = None,
    event_bus_arn: Annotated[
        Optional[str],
        typer.Option("--event-bus-arn", help="Central EventBridge event bus ARN.", envvar="MESH_EVENT_BUS_ARN"),
    ] = None,
) -> None:
    """Restore a gold Iceberg table to a prior snapshot via Glue time travel.

    This command will:

    \b
    1. Validate product exists and is not DEPRECATED or RETIRED
    2a. With --list-snapshots: show available Iceberg snapshots
    2b. With --to-snapshot <id>: acquire pipeline lock, start Glue rollback job, release lock
    3. Emit ProductRefreshed event and update row_count/last_refreshed in mesh-products

    Examples:

    \b
      datameshy product rollback sales/customer_orders --list-snapshots
      datameshy product rollback sales/customer_orders --to-snapshot 8765309
    """
    if "/" not in product_ref:
        console.print("[red]product_ref must be in the format <domain>/<product>.[/red]")
        raise typer.Exit(code=1)

    if not list_snapshots and to_snapshot is None:
        console.print("[red]Provide --to-snapshot <id> or --list-snapshots.[/red]")
        raise typer.Exit(code=1)

    domain, product_name = product_ref.split("/", 1)
    product_id = f"{domain}#{product_name}"

    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")
        products_table = dynamodb.Table(_PRODUCTS_TABLE)

        # 1. Fetch product
        response = products_table.get_item(Key={"product_id": product_id})
        item = response.get("Item")
        if not item:
            console.print(f"[red]Product '{domain}/{product_name}' not found in mesh-products.[/red]")
            raise typer.Exit(code=1)

        current_status = item.get("status", "")
        if current_status in ("DEPRECATED", "RETIRED"):
            console.print(
                f"[red]Rollback blocked: product '{domain}/{product_name}' has status={current_status}.[/red]"
            )
            raise typer.Exit(code=1)

        glue_db = item.get("glue_catalog_db_gold", f"{domain}_gold")
        table_name = product_name

        if list_snapshots:
            # List snapshots via Glue/Athena query
            console.print(f"\n[bold]Iceberg snapshots for {domain}/{product_name}:[/bold]")
            console.print(
                f"[dim]Run the following Athena query to view snapshots:[/dim]\n"
                f"  SELECT snapshot_id, committed_at, operation\n"
                f"  FROM \"{glue_db}\".\"{table_name}$snapshots\"\n"
                f"  ORDER BY committed_at DESC;"
            )
            console.print(
                "\n[dim]Or use the Glue console / boto3 glue_catalog.{db}.{table}.snapshots table.[/dim]"
            )
            return

        # 2. Rollback path: acquire lock, start Glue job, release lock
        locks_table = dynamodb.Table(_LOCKS_TABLE)
        lock_response = locks_table.get_item(Key={"product_id": product_id})
        lock_item = lock_response.get("Item")
        if lock_item and lock_item.get("locked"):
            console.print(
                f"[yellow]Pipeline is already locked for '{domain}/{product_name}'.[/yellow]\n"
                "Wait for the current operation to finish."
            )
            raise typer.Exit(code=1)

        # Acquire lock
        locks_table.put_item(Item={
            "product_id": product_id,
            "locked": True,
            "operation": "rollback",
            "snapshot_id": to_snapshot,
        })
        console.print(f"[dim]Pipeline lock acquired for {product_id}.[/dim]")

        try:
            if glue_job_name:
                glue = session.client("glue")
                run_response = glue.start_job_run(
                    JobName=glue_job_name,
                    Arguments={
                        "--domain": domain,
                        "--product_name": product_name,
                        "--gold_db": glue_db,
                        "--table_name": table_name,
                        "--snapshot_id": str(to_snapshot),
                    },
                )
                job_run_id = run_response.get("JobRunId", "unknown")
                console.print(f"[green]Glue rollback job started.[/green] run_id={job_run_id}")
            else:
                console.print(
                    f"[yellow]No Glue job configured (--glue-job-name not set).[/yellow]\n"
                    f"  Would execute: CALL glue_catalog.system.rollback_to_snapshot("
                    f"'{glue_db}.{table_name}', {to_snapshot})"
                )

            # Update catalog metadata
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            products_table.update_item(
                Key={"product_id": product_id},
                UpdateExpression="SET last_refreshed = :now, last_rollback_snapshot = :snap",
                ExpressionAttributeValues={":now": now_str, ":snap": to_snapshot},
            )

            # Emit ProductRefreshed event
            if event_bus_arn:
                try:
                    from datameshy.lib.aws_client import put_mesh_event

                    put_mesh_event(
                        session=session,
                        event_bus_arn=event_bus_arn,
                        event_type="ProductRefreshed",
                        payload={
                            "domain": domain,
                            "product_name": product_name,
                            "product_id": product_id,
                            "rollback_snapshot_id": to_snapshot,
                        },
                    )
                    console.print("[green]ProductRefreshed event emitted.[/green]")
                except Exception as exc:
                    console.print(f"[yellow]Warning: could not emit ProductRefreshed event: {exc}[/yellow]")

            console.print(f"\n[bold green]Rollback to snapshot {to_snapshot} initiated for '{domain}/{product_name}'.[/bold green]")

        finally:
            # Release lock
            locks_table.delete_item(Key={"product_id": product_id})
            console.print(f"[dim]Pipeline lock released for {product_id}.[/dim]")

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error during rollback:[/red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command("import")
def product_import(
    ctx: typer.Context,
    spec: Annotated[
        Path,
        typer.Option("--spec", help="Path to product.yaml spec file.", exists=True, readable=True),
    ],
    glue_database: Annotated[str, typer.Option("--glue-database", help="Glue catalog database name.")],
    glue_table: Annotated[str, typer.Option("--glue-table", help="Glue catalog table name.")],
    event_bus_arn: Annotated[
        Optional[str],
        typer.Option("--event-bus-arn", help="Central EventBridge event bus ARN.", envvar="MESH_EVENT_BUS_ARN"),
    ] = None,
    lf_grantor_role_arn: Annotated[
        Optional[str],
        typer.Option("--lf-grantor-role-arn", help="ARN of MeshLFGrantorRole.", envvar="MESH_LF_GRANTOR_ROLE_ARN"),
    ] = None,
) -> None:
    """Register an existing Glue-catalogued Iceberg table as a mesh data product.

    This command will:

    \b
    1. Validate product.yaml spec (same path as product create)
    2. Verify the Glue database and table exist in the domain account
    3. Verify the table is Iceberg format
    4. Apply LF-Tags (classification, pii, domain) via MeshLFGrantorRole
    5. Write catalog entry to mesh-products with status=ACTIVE, import_source=glue
    6. Emit ProductCreated event

    Example:

    \b
      datameshy product import \\
        --spec examples/sales-domain/products/revenue_daily/product.yaml \\
        --glue-database sales_domain --glue-table revenue_daily
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
    description = parsed["product"].get("description", "")
    schema_version = parsed.get("schema_version", 1)
    classification = parsed.get("classification", "internal")
    has_pii = any(c.get("pii", False) for c in parsed.get("schema", {}).get("columns", []))

    console.print(f"  [green]Spec valid.[/green] Product: {domain_name}/{product_name}")

    try:
        session = _get_session_from_ctx(ctx)
        dynamodb = session.resource("dynamodb")
        products_table = dynamodb.Table(_PRODUCTS_TABLE)
        product_id = f"{domain_name}#{product_name}"

        # Duplicate guard
        existing = products_table.get_item(Key={"product_id": product_id})
        if existing.get("Item"):
            console.print(
                f"[red]Product '{domain_name}/{product_name}' is already registered in mesh-products.[/red]\n"
                "Remove the existing entry or use a different product name."
            )
            raise typer.Exit(code=1)

        # 2. Verify Glue database + table exist
        console.print(f"\n[bold]Verifying Glue table:[/bold] {glue_database}.{glue_table}")
        glue = session.client("glue")
        try:
            table_response = glue.get_table(DatabaseName=glue_database, Name=glue_table)
            glue_table_obj = table_response["Table"]
        except glue.exceptions.EntityNotFoundException:
            console.print(
                f"[red]Glue table not found:[/red] {glue_database}.{glue_table}\n"
                "Verify the database and table names and that you have the correct AWS credentials."
            )
            raise typer.Exit(code=1)
        except Exception as exc:
            console.print(f"[red]Error looking up Glue table:[/red] {exc}")
            raise typer.Exit(code=1) from exc

        # 3. Verify Iceberg format
        table_params = glue_table_obj.get("Parameters", {})
        table_type = table_params.get("table_type", "").upper()
        if table_type != "ICEBERG":
            console.print(
                f"[red]Table '{glue_database}.{glue_table}' is not an Iceberg table (table_type={table_type or 'unknown'}).[/red]\n"
                "Only Iceberg-format tables can be imported as mesh products."
            )
            raise typer.Exit(code=1)
        console.print(f"  [green]Iceberg table confirmed.[/green]")

        # 4. Apply LF-Tags (best-effort)
        if lf_grantor_role_arn:
            try:
                lf = session.client("lakeformation")
                lf.add_lf_tags_to_resource(
                    Resource={
                        "Table": {
                            "DatabaseName": glue_database,
                            "Name": glue_table,
                        }
                    },
                    LFTags=[
                        {"TagKey": "classification", "TagValues": [classification]},
                        {"TagKey": "pii", "TagValues": ["true" if has_pii else "false"]},
                        {"TagKey": "domain", "TagValues": [domain_name]},
                    ],
                )
                console.print("[green]LF-Tags applied.[/green]")
            except Exception as exc:
                console.print(f"[yellow]Warning: could not apply LF-Tags: {exc}[/yellow]")
        else:
            console.print("[dim]--lf-grantor-role-arn not set; skipping LF-Tag application.[/dim]")

        # 5. Write catalog entry
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        products_table.put_item(Item={
            "product_id": product_id,
            "domain": domain_name,
            "product_name": product_name,
            "owner": owner,
            "description": description,
            "status": "ACTIVE",
            "import_source": "glue",
            "glue_database": glue_database,
            "glue_table": glue_table,
            "schema_version": schema_version,
            "classification": classification,
            "pii": has_pii,
            "imported_at": now_str,
        })
        console.print(f"[green]Catalog entry written to mesh-products.[/green] status=ACTIVE, import_source=glue")

        # 6. Emit ProductCreated event
        if event_bus_arn:
            try:
                from datameshy.lib.aws_client import put_mesh_event

                put_mesh_event(
                    session=session,
                    event_bus_arn=event_bus_arn,
                    event_type="ProductCreated",
                    payload={
                        "domain": domain_name,
                        "product_name": product_name,
                        "product_id": product_id,
                        "owner": owner,
                        "schema_version": schema_version,
                        "import_source": "glue",
                    },
                )
                console.print("[green]ProductCreated event emitted.[/green]")
            except Exception as exc:
                console.print(f"[yellow]Warning: could not emit ProductCreated event: {exc}[/yellow]")

        console.print(
            f"\n[bold green]Product '{domain_name}/{product_name}' imported successfully.[/bold green]\n"
            f"  Glue source: {glue_database}.{glue_table}\n"
            f"  Status: ACTIVE\n"
            f"\nNext steps:\n"
            f"  1. Check status: [cyan]datameshy product status --domain {domain_name} --name {product_name}[/cyan]\n"
            f"  2. Search in catalog: [cyan]datameshy catalog search --keyword {product_name}[/cyan]"
        )

    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f"[red]Error importing product:[/red] {exc}")
        raise typer.Exit(code=1) from exc
