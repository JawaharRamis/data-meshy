# Guide: Customize Pipeline Transforms

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

## Goal

Customize the three Glue PySpark jobs (raw ingestion, silver transform, gold aggregate) for your domain's specific data sources, business rules, and enrichment logic. This guide covers where to add customizations in the template files and how to deploy the updated scripts.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Product created | `datameshy product create` has been run and infrastructure is provisioned. |
| Domain engineer AWS access | SSO profile with `DomainDataEngineer` permission set. |
| Understanding of source data | You know the schema, format, and location of your source data. |
| Familiarity with PySpark | Glue jobs use PySpark on the Glue 4.0 runtime. |

---

## Pipeline Architecture Overview

The medallion pipeline runs three Glue jobs in sequence via Step Functions:

```
Raw Ingestion (Bronze) -> Silver Transform (Validated) -> Gold Aggregate (Data Product)
```

| Job | Input | Output | Purpose |
|---|---|---|---|
| `raw_ingestion.py` | Source system (JDBC or S3) | Raw S3 bucket (Parquet) | Land source data as-is with ingestion metadata |
| `silver_transform.py` | Raw S3 bucket (Parquet) | Silver Iceberg table | Validate, dedup, enforce schema |
| `gold_aggregate.py` | Silver Iceberg table | Gold Iceberg table | Business logic, enrichment, final data product |

Each job receives parameters from the Step Functions state machine input.

---

## Steps

### 1. Understand the Template Structure

The template files are located at `templates/glue_jobs/`. The customer_orders example shows customized versions at `examples/example-domain-repo/products/customer_orders/`.

Each template has clearly marked customization sections:

```
# --- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---
# (your code goes here)
# --- END DOMAIN-SPECIFIC TRANSFORMS ---
```

Copy templates to your product directory before modifying:

```bash
mkdir -p examples/example-domain-repo/products/my_product/

cp templates/glue_jobs/raw_ingestion.py   examples/example-domain-repo/products/my_product/
cp templates/glue_jobs/silver_transform.py examples/example-domain-repo/products/my_product/
cp templates/glue_jobs/gold_aggregate.py  examples/example-domain-repo/products/my_product/
```

---

### 2. Customize raw_ingestion.py (Add a Source Connector)

**Goal**: Configure how data is read from your source system.

The template supports two source types: S3 (Parquet files) and JDBC (via Glue Connection). The source type is selected by the `--source_connection_name` parameter.

#### Template Parameters

| Parameter | Required | Description |
|---|---|---|
| `--domain` | Yes | Domain name |
| `--product_name` | Yes | Product name |
| `--raw_bucket` | Yes | Raw S3 bucket name |
| `--source_connection_name` | Yes | Glue connection name or `S3` for S3 source |
| `--raw_db` | Yes | Glue catalog database for raw layer |
| `--table_name` | Yes | Target table name |
| `--source_s3_path` | If source is S3 | S3 path to source files |
| `--source_table` | No | JDBC source table name (default: same as `table_name`) |
| `--source_query` | No | Custom SQL query for partial extraction |
| `--ingestion_date` | No | Override ingestion date partition (YYYY-MM-DD) |
| `--max_rows` | No | Cap rows read (useful for testing) |

#### Customization: Add an S3 Source

The template handles S3 sources out of the box. Set the Glue job parameter:

```
--source_connection_name S3 --source_s3_path s3://my-bucket/data/orders/
```

No code changes needed.

#### Customization: Add a JDBC Source

1. Create a Glue Connection in the AWS Console (or via Terraform) pointing to your database.
2. Set the parameter: `--source_connection_name my_db_connection --source_table public.orders`

The template reads via JDBC:

```python
dynamic_frame = glue_context.create_dynamic_frame.from_options(
    connection_type="jdbc",
    connection_options={
        "connectionName": source_connection,
        "useConnectionProperties": "true",
        "dbtable": source_table,
    },
    transformation_ctx="raw_jdbc_source",
)
```

#### Customization: Add a Custom Source (API, Kinesis, etc.)

Replace the source reading block (between the "Read source data" and "Add ingestion metadata" sections). Example for reading from an API via requests:

```python
import requests

# Fetch data from REST API
response = requests.get(
    "https://api.internal.company.com/v1/orders",
    headers={"Authorization": f"Bearer {api_token}"},
)
records = response.json()

# Convert to Spark DataFrame
df = spark.createDataFrame(records)
```

#### Customization: Change the Output Format

The template writes to Parquet partitioned by `_ingestion_date`. To write directly to an Iceberg table instead (as the customer_orders example does):

```python
raw_table = f"{args['raw_db']}.{args['table_name']}"
df.writeTo(raw_table).using("iceberg").tableProperty(
    "format-version", "2"
).createOrReplace()
```

#### What NOT to Change

- The `_ingestion_date`, `_ingestion_ts`, and `_job_run_id` metadata columns -- these are used by downstream jobs for deduplication.
- The `job.commit()` call at the end -- this is required for job bookmark progress.
- The structured logging format -- this feeds into CloudWatch dashboards.

---

### 3. Customize silver_transform.py (Add Business Rules)

**Goal**: Add validation, deduplication, type enforcement, and schema checks for your data.

#### Template Parameters

| Parameter | Required | Description |
|---|---|---|
| `--domain` | Yes | Domain name |
| `--product_name` | Yes | Product name |
| `--raw_bucket` | Yes | Raw S3 bucket name |
| `--silver_bucket` | Yes | Silver S3 bucket name |
| `--raw_db` | Yes | Glue catalog DB for raw layer |
| `--silver_db` | Yes | Glue catalog DB for silver layer |
| `--table_name` | Yes | Table name |
| `--quality_ruleset_name` | Yes | Glue DQ ruleset name |
| `--primary_key_columns` | No | Comma-separated PK columns for dedup |
| `--expected_columns` | No | Comma-separated expected column names |
| `--ingestion_date` | No | Process only this date partition |
| `--enable_quality_check` | No | `true`/`false` (default: `true`) |

#### Customization Points in the Template

The silver template performs these steps in order. Each has a customization point:

**Step 1: Read raw data** (line ~120)
- Default: Reads Parquet from raw S3.
- Customize: If raw was written as Iceberg, read from the Iceberg table instead.

```python
# Read from raw Iceberg table instead of Parquet files
raw_table = f"{args['raw_db']}.{args['table_name']}"
df_raw = spark.table(f"glue_catalog.{raw_table}")
```

**Step 2: Drop metadata columns** (line ~137)
- Default: Drops `_ingestion_date`, `_ingestion_ts`, `_job_run_id`.
- Customize: If you added custom metadata columns in raw_ingestion, drop them here too.

```python
metadata_cols = {"_ingestion_date", "_ingestion_ts", "_job_run_id", "_my_custom_col"}
```

**Step 3: Column name standardization** (line ~147)
- Default: Lowercases, strips whitespace, replaces spaces and hyphens with underscores.
- No customization needed unless you have specific column naming requirements.

**Step 4: Deduplication** (line ~157)
- Default: Deduplicates by `--primary_key_columns` using a window function keeping the latest by `_ingestion_ts`.
- Customize: Change the dedup logic. For example, keep the record with the highest version:

```python
# Custom dedup: keep the record with the highest version number
window = Window.partitionBy(*pk_cols).orderBy(F.col("version").desc())
df = df_raw.select(data_cols + ["version"]).withColumn("_rn", F.row_number().over(window))
df = df.filter(F.col("_rn") == 1).drop("_rn", "version")
```

**Step 5: Null checks** (line ~179)
- Default: Validates that expected columns exist in the DataFrame.
- Customize: Add domain-specific validation. For example, check value ranges:

```python
# Custom validation: reject negative amounts
if "amount" in df.columns:
    invalid = df.filter(F.col("amount") < 0).count()
    if invalid > 0:
        log("WARN", f"Dropping {invalid} rows with negative amounts")
        df = df.filter(F.col("amount") >= 0)
```

**Step 6: Glue Data Quality evaluation** (line ~241)
- Default: Runs the DQDL ruleset defined in `product.yaml`.
- This is automatic. Quality rules are defined in `product.yaml`, not in the Glue job code.

#### What NOT to Change

- The Iceberg write configuration (`format-version: 2`, target file size 128MB).
- The schema validation block (compares live schema vs expected columns).
- The `job.commit()` call.

---

### 4. Customize gold_aggregate.py (Add Enrichment Logic)

**Goal**: Add business logic that transforms silver data into the final consumable data product.

#### Template Parameters

| Parameter | Required | Description |
|---|---|---|
| `--domain` | Yes | Domain name |
| `--product_name` | Yes | Product name |
| `--silver_bucket` | Yes | Silver S3 bucket name |
| `--gold_bucket` | Yes | Gold S3 bucket name |
| `--silver_db` | Yes | Glue catalog DB for silver layer |
| `--gold_db` | Yes | Glue catalog DB for gold layer |
| `--table_name` | Yes | Table name |
| `--primary_key_columns` | No | Comma-separated PK columns for MERGE (default: `id`) |
| `--partition_by_columns` | No | Comma-separated columns for Iceberg partitioning |
| `--declared_columns` | No | Comma-separated expected output columns from `product.yaml` |
| `--cloudwatch_namespace` | No | CloudWatch namespace for metrics (default: `DataMeshy/Pipeline`) |

#### The Customization Section

The gold template has a clearly marked section between `--- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---` and `--- END DOMAIN-SPECIFIC TRANSFORMS ---`. The template passes data through unchanged (identity transform) as a starting point.

**Example 1: Add a derived column (from customer_orders)**

```python
# --- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---

def assign_customer_segment(order_total):
    return (
        F.when(F.col("order_total") >= 500, F.lit("Gold"))
        .when(F.col("order_total") >= 200, F.lit("Silver"))
        .otherwise(F.lit("Bronze"))
    )

df_gold = df_silver.withColumn("customer_segment", assign_customer_segment(F.col("order_total")))

# --- END DOMAIN-SPECIFIC TRANSFORMS ---
```

**Example 2: Join with a reference table**

```python
# --- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---

# Read a reference table from S3
ref_path = "s3://sales-raw-ACCOUNT_ID/reference/product_categories/"
df_categories = spark.read.parquet(ref_path)

# Enrich with category name
df_gold = df_silver.join(
    df_categories.select("product_id", "category_name"),
    on="product_id",
    how="left"
)

# --- END DOMAIN-SPECIFIC TRANSFORMS ---
```

**Example 3: Aggregation**

```python
# --- BEGIN DOMAIN-SPECIFIC TRANSFORMS ---

df_gold = (
    df_silver
    .groupBy("customer_id", "order_date")
    .agg(
        F.sum("order_total").alias("daily_spend"),
        F.count("order_id").alias("order_count"),
        F.avg("order_total").alias("avg_order_value"),
    )
)

# --- END DOMAIN-SPECIFIC TRANSFORMS ---
```

#### Schema Validation

The gold job includes undeclared column detection. If `--declared_columns` is provided (the CLI sets this automatically from `product.yaml`), any column in the output DataFrame that is not listed will cause an `UndeclaredColumnError` and block publish.

This means: when you add a new column in the gold transform, you MUST also add it to `product.yaml` under `schema.columns`. Otherwise the pipeline will fail at the schema validation step.

Steps when adding a new output column:
1. Add the column logic in the gold transform
2. Add the column definition to `product.yaml` in `schema.columns`
3. Increment `schema_version` in `product.yaml`
4. Run `datameshy product create --spec product.yaml` to update the infrastructure
5. Run `datameshy product refresh` to test

#### Upsert Behavior

The gold job uses Iceberg `MERGE INTO` for upsert semantics. The `--primary_key_columns` parameter defines the match condition. On subsequent runs:
- Existing records (matched by PK) are updated
- New records are inserted
- No records are deleted

If you need to handle deletions, customize the MERGE SQL in the template to add a `WHEN MATCHED AND s._deleted = true THEN DELETE` clause.

#### What NOT to Change

- The silver metadata column removal (`_silver_ts`, `_silver_job_run_id`).
- The gold metadata column addition (`_gold_ts`, `_gold_job_run_id`).
- The `UndeclaredColumnError` validation block.
- The CloudWatch metric emission.
- The `job.commit()` call.

---

### 5. Deploy Updated Scripts

After customizing, upload the updated scripts to S3:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --profile sales-engineer --query Account --output text)

aws s3 cp examples/example-domain-repo/products/my_product/raw_ingestion.py \
  s3://sales-raw-${ACCOUNT_ID}/pipeline-code/my_product/raw_ingestion.py \
  --profile sales-engineer

aws s3 cp examples/example-domain-repo/products/my_product/silver_transform.py \
  s3://sales-raw-${ACCOUNT_ID}/pipeline-code/my_product/silver_transform.py \
  --profile sales-engineer

aws s3 cp examples/example-domain-repo/products/my_product/gold_aggregate.py \
  s3://sales-raw-${ACCOUNT_ID}/pipeline-code/my_product/gold_aggregate.py \
  --profile sales-engineer
```

### 6. Test the Pipeline

Run a pipeline refresh to test the updated transforms:

```bash
datameshy --profile sales-engineer product refresh \
  --domain sales \
  --name my_product
```

If the pipeline fails, check the Glue job logs in CloudWatch:

```bash
aws logs get-log-events \
  --log-group-name /aws-glue/jobs/output \
  --log-stream-name <job-run-id> \
  --profile sales-engineer
```

The structured JSON logs from each job include `domain`, `product_name`, `layer`, and `run_id` for easy filtering.

---

## Verify

| Check | Expected Result |
|---|---|
| Pipeline completes without errors | All three jobs succeed |
| Quality score >= `minimum_quality_score` | All DQDL rules pass |
| Gold table has expected columns | Matches `schema.columns` in `product.yaml` |
| No `UndeclaredColumnError` | All output columns are declared |
| CloudWatch metrics emitted | `GoldRowsWritten`, `GoldRowsUpdated` visible in metrics |
| Athena query returns data | `SELECT * FROM {domain}_gold.{product} LIMIT 10` works |

---

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `UndeclaredColumnError` | Output DataFrame has columns not in `product.yaml` | Add the column to `schema.columns` in `product.yaml` and increment `schema_version`. |
| `SchemaValidationError` in silver | Raw data missing expected columns | Check source data schema. Either fix the source or add the missing columns to `product.yaml`. |
| `QualityCheckFailedError` | DQDL rules failed | Check the `failed_rules` list in the job log. Adjust rules in `product.yaml` or fix source data quality. |
| Glue job timeout (30 min) | Dataset too large or transform too complex | Increase DPU count (up to 4 per SCP). For larger datasets, consider EMR Serverless. |
| MERGE INTO fails | PK columns not unique in source | Ensure `--primary_key_columns` uniquely identifies rows. Check for duplicates in silver layer. |
| `ModuleNotFoundError` in Glue job | Missing Python package | Glue 4.0 supports additional Python packages. Add them via `--additional-python-modules` job parameter. |
| Iceberg write fails with "table not found" | Gold table not yet created | First run uses `createOrReplace()`. Ensure the Glue database (`{domain}_gold`) exists. |
| `QualityAlert` event but pipeline succeeds | Quality score below threshold but above hard failure | Check `minimum_quality_score` in `product.yaml`. The pipeline blocks publish if score is below this value. |

---

## See Also

- [Add a Product Guide](ADD-PRODUCT.md) -- product creation workflow
- [Product Spec Reference](../reference/PRODUCT-SPEC.md) -- quality rules and schema fields
- [Resource Naming Reference](../reference/RESOURCE-NAMING.md) -- S3 bucket and Glue database names
- Template files: `templates/glue_jobs/*.py`
- Example files: `examples/example-domain-repo/products/customer_orders/glue_jobs/`
