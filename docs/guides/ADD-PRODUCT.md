# Guide: Add a Data Product

> **Phase coverage**: Phase 1 | **Last updated**: 2026-04-03

## Navigation
<- [Docs home](../README.md)

---

## Goal

Create a new data product within an existing domain by writing the `product.yaml` spec, validating it, provisioning the infrastructure, customizing the Glue jobs, and running the first pipeline refresh.

---

## Prerequisites

| Requirement | Details |
|---|---|
| Domain onboarded | The domain must be registered and its infrastructure deployed (see [Add a Domain](ADD-DOMAIN.md)). |
| AWS SSO access | A profile with `DomainDataEngineer` permission set for the domain account. |
| `datameshy` CLI installed | Version 0.1.0+. |
| Source data accessible | Source system reachable from the domain account (JDBC connection or S3 path). |

---

## Steps

### 1. Write the product.yaml

Start from the template:

```bash
cp templates/product_spec/product.yaml.template \
   examples/example-domain-repo/products/my_new_product/product.yaml
```

Edit the file with your product's details. Every field is documented in the [Product Spec Reference](../reference/PRODUCT-SPEC.md). The minimum required structure:

```yaml
schema_version: 1

product:
  name: my_new_product
  domain: sales
  description: "Description of the data product"
  owner: team@company.com

schema:
  format: iceberg
  columns:
    - name: id
      type: string
      description: "Primary key"
      pii: false
      nullable: false
    # Add more columns...

quality:
  rules:
    - name: id_complete
      rule: "IsComplete 'id'"
      threshold: 1.0

classification: internal

sla:
  refresh_frequency: daily
```

Key decisions to make:

| Decision | Field | Guidance |
|---|---|---|
| What columns to publish? | `schema.columns` | Only include columns that should be visible to consumers. PII columns get `pii: true`. |
| How to partition? | `schema.partition_by` | Partition by the most common filter column (usually a date). Use `month` transform for daily data. |
| Quality threshold? | `quality.minimum_quality_score` | Start at 95. Lower if source data is messy. |
| Who can access? | `classification` | `internal` for most products. `confidential` if any PII. |
| How often to refresh? | `sla.refresh_frequency` | `daily` is most common. `on_demand` for static reference data. |

### 2. Validate the Spec

Validate against the JSON Schema before provisioning:

```bash
datameshy --profile sales-engineer product create \
  --spec examples/example-domain-repo/products/my_new_product/product.yaml \
  --dry-run
```

The `--dry-run` flag validates the spec without creating any resources. It checks:

- All required fields are present (`schema_version`, `product`, `sla`, `schema`, `quality`, `classification`)
- Column names match the snake_case pattern
- Types are valid Spark SQL types
- Classification is one of the allowed values
- Quality rules are non-empty

If validation fails, fix the errors shown and re-run.

### 3. Run `datameshy product create`

Once the spec is valid, provision the product:

```bash
datameshy --profile sales-engineer product create \
  --spec examples/example-domain-repo/products/my_new_product/product.yaml \
  --event-bus-arn "arn:aws:events:us-east-1:CENTRAL_ACCOUNT_ID:event-bus/mesh-central-bus"
```

The CLI performs these steps:

1. **Validates the spec** against `schemas/product_spec.json`
2. **Checks the product does not already exist** in the `mesh-products` DynamoDB table
3. **Uploads Glue job templates** to the raw S3 bucket under `pipeline-code/{product_name}/`
4. **Runs `terraform plan`** for the `data-product` module with variables extracted from the spec
5. **Prompts for confirmation** before applying
6. **Runs `terraform apply`** which provisions:
   - Iceberg table in the gold Glue catalog database
   - Glue Data Quality ruleset (`{domain}_{product}_dq`)
   - Step Functions state machine (`{domain}-{product}-pipeline`)
   - Secrets Manager secret for source credentials
   - LF-Tags on the table (`classification`, `pii`, `domain`)
7. **Emits a `ProductCreated` event** to the central EventBridge bus

### 4. Set Up Source Credentials

If your product reads from a JDBC source, store the credentials in Secrets Manager:

```bash
aws secretsmanager put-secret-value \
  --secret-id mesh/sales/my_source-credentials \
  --secret-string '{"username":"read_only_user","password":"...","host":"...","port":5432,"database":"orders"}' \
  --profile sales-engineer
```

The secret ARN should match what is specified in `product.yaml` under `lineage.sources[].credentials_secret_arn`.

### 5. Customize the Glue Jobs

The template Glue jobs are copied to S3 but need customization for your specific data source and transforms. Copy the templates to your product directory and modify them:

```bash
mkdir -p examples/example-domain-repo/products/my_new_product/

cp templates/glue_jobs/raw_ingestion.py examples/example-domain-repo/products/my_new_product/
cp templates/glue_jobs/silver_transform.py examples/example-domain-repo/products/my_new_product/
cp templates/glue_jobs/gold_aggregate.py examples/example-domain-repo/products/my_new_product/
```

See the [Customize Pipeline Guide](CUSTOMIZE-PIPELINE.md) for detailed instructions on modifying each job.

After customizing, upload the updated scripts:

```bash
aws s3 cp examples/example-domain-repo/products/my_new_product/raw_ingestion.py \
  s3://sales-raw-ACCOUNT_ID/pipeline-code/my_new_product/raw_ingestion.py \
  --profile sales-engineer

aws s3 cp examples/example-domain-repo/products/my_new_product/silver_transform.py \
  s3://sales-raw-ACCOUNT_ID/pipeline-code/my_new_product/silver_transform.py \
  --profile sales-engineer

aws s3 cp examples/example-domain-repo/products/my_new_product/gold_aggregate.py \
  s3://sales-raw-ACCOUNT_ID/pipeline-code/my_new_product/gold_aggregate.py \
  --profile sales-engineer
```

### 6. Run the First Pipeline Refresh

```bash
datameshy --profile sales-engineer product refresh \
  --domain sales \
  --name my_new_product
```

The CLI will:

1. Look up the product in `mesh-products` DynamoDB
2. Check the pipeline is not already locked
3. Start the Step Functions state machine with the correct input parameters
4. Wait for completion with a progress spinner
5. Display the quality score and rows written on success

### 7. Verify the Product

```bash
datameshy --profile sales-engineer product status \
  --domain sales \
  --name my_new_product
```

Query the data:

```sql
SELECT * FROM sales_gold.my_new_product LIMIT 10;
```

---

## Verify

| Check | Expected Result |
|---|---|
| `datameshy product status` shows `ACTIVE` | Product is registered and refreshed |
| Quality score >= `minimum_quality_score` | Data passed all DQDL rules |
| Rows written > 0 | Data flowed through the pipeline |
| `ProductRefreshed` event emitted | Check EventBridge metrics or `mesh-audit-log` |
| Iceberg table exists in Glue Catalog | `sales_gold.my_new_product` is queryable via Athena |
| LF-Tags applied | Table has `classification`, `pii`, `domain` tags |

---

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| `Spec validation failed` | Missing or invalid fields | Check required fields: `schema_version`, `product.name`, `product.domain`, `product.owner`, `sla.refresh_frequency`, `schema.columns`, `quality.rules`, `classification`. |
| `Product already exists` | Duplicate product name in domain | Use a different name, or use `datameshy product refresh` if updating an existing product. |
| `Pipeline is already running` | Concurrent execution lock active | Wait for current run to finish. Check `mesh-pipeline-locks` table. Locks auto-expire after 3 hours (TTL). |
| `Quality check failed` | DQDL rules below threshold | Review the `failed_rules` in the `QualityAlert` event. Adjust data quality or lower `minimum_quality_score`. |
| `UndeclaredColumnError` in gold job | Output has columns not in `product.yaml` | Either add the column to the spec, or remove it from the transform logic. |
| `SchemaValidationError` in silver job | Raw data missing expected columns | Check source data matches the expected schema. Add the column to `product.yaml` if it should exist. |
| Glue job fails with connection error | JDBC source not reachable | Verify the Glue connection exists and the VPC/subnet configuration is correct. Check the secret in Secrets Manager. |
| `terraform plan` shows no changes | Product already provisioned | This is normal if the product was already created. Use `datameshy product refresh` to run the pipeline. |

---

## See Also

- [Product Spec Reference](../reference/PRODUCT-SPEC.md) -- complete field documentation
- [Customize Pipeline Guide](CUSTOMIZE-PIPELINE.md) -- modifying Glue job transforms
- [Event Schemas Reference](../reference/EVENT-SCHEMAS.md) -- events emitted during product lifecycle
- [Quick Start Guide](QUICK-START.md) -- end-to-end walkthrough
- Product template: `templates/product_spec/product.yaml.template`
- Example product: `examples/example-domain-repo/products/customer_orders/`
