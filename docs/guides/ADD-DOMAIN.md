# Guide: Add a New Domain

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

<- [Docs home](../README.md) | [Add a Product](ADD-PRODUCT.md) | [Subscription Flow](subscription-flow.md)

---

## Goal

Register a new domain with the data mesh platform. This guide is for **platform engineers only**. Domain onboarding provisions cross-account IAM roles, creates the domain's GitHub repo from the platform template, registers the domain in `domains/`, and opens a quickstart checklist for the domain team. Domain teams never touch this workflow.

---

## How it works

All domain onboarding is driven by a single GitHub Actions workflow: `onboard-domain.yml` in this repository. You fill in six inputs in the GitHub Actions UI, click Run, and the workflow does everything. There is no Terraform to run locally, no HCL to write, and no pip CLI.

The workflow runs four jobs in sequence:

| Job | What it does |
|---|---|
| `validate` | Checks inputs (name pattern, account ID format, email format). Fails fast before any AWS calls. |
| `provision-iam` | Assumes `MeshOnboardingRole`, then chains into the domain account. Creates GitHub OIDC provider, `DomainGitHubActionsRole`, and `MeshEventRole`. Commits `domains/{domain_name}.yaml` to main. Rolls back the commit if IAM provisioning fails. |
| `provision-aws` | Registers the Lake Formation S3 location (best-effort). Creates the EventBridge cross-account forwarding rule (best-effort). |
| `bootstrap-repo` | Instantiates `data-meshy-product-template` as the new DP repo, sets three repo secrets, and opens the quickstart checklist issue. |

---

## Prerequisites

Before triggering the workflow, confirm all of the following:

| Requirement | How to verify |
|---|---|
| Central governance is deployed | `infra/environments/central/` has been applied; `mesh-tf-state` S3 bucket exists. |
| `MeshOnboardingRole` exists in central account | Check IAM in the central governance account — this role is provisioned by the governance module. |
| `ORG_PAT` secret is set on this repo | GitHub → Settings → Secrets → Actions → `ORG_PAT` present. This token needs `repo` and `admin:org` scopes for the GitHub org. |
| AWS account is in the org | The 12-digit account ID belongs to the AWS Organization managed by the platform. |
| Domain name is not already registered | Check `domains/` in this repo — no file named `{domain_name}.yaml` should exist. |
| GitHub org access | You have permission to trigger `workflow_dispatch` on `onboard-domain.yml` in this repo. |

---

## Step 1: Preflight checks

Before triggering the workflow, do a quick sanity check:

**Check the domain does not already exist:**

```
ls domains/
```

If `{domain_name}.yaml` is already present, the domain is already registered. Do not re-run onboarding — it will fail at the validate job.

**Check the AWS account is not already onboarded:**

```
python tools/catalog.py browse --domain <name>
```

(Platform engineers only. If the command returns a record, the domain is already in the catalog.)

**Confirm the account ID:**

Get the exact 12-digit AWS account ID for the domain account. The `onboard-domain.yml` workflow validates the format but cannot verify org membership at input time — a wrong account ID will fail during the IAM provisioning job.

---

## Step 2: Trigger `onboard-domain.yml`

Navigate to **Actions → onboard-domain.yml → Run workflow** in this repository.

Fill in all six inputs:

### `domain_name` — required

The snake_case identifier for the domain. This becomes the primary key in `domains/`, the prefix for all domain resources, and the `MESH_DOMAIN_NAME` secret in the DP repo.

- Pattern: `^[a-z][a-z0-9_]*$` (must start with a letter; only lowercase letters, digits, underscores)
- Examples: `sales`, `marketing`, `supply_chain`, `hr_analytics`
- Maximum length: 63 characters (AWS resource naming limit)
- Cannot be changed after onboarding without manual cleanup

### `account_id` — required

The 12-digit AWS account ID for the domain's dedicated AWS account.

- Format: exactly 12 digits, no hyphens or spaces
- Example: `123456789012`
- This account must already exist in your AWS Organization before onboarding

### `aws_region` — required

The primary AWS region where the domain's infrastructure will be deployed.

- Format: standard AWS region slug
- Example: `ap-southeast-2`, `us-east-1`, `eu-west-1`
- Must match the region where central governance is deployed, or be a region with EventBridge cross-region rules configured

### `owner_email` — required

The email address of the domain data owner. This address receives workflow failure notifications and appears in the `domains/` registry.

- Format: valid email address
- Example: `jawahar@acme.com`, `sales-data-team@acme.com`

### `github_repo` — required

The `org/repo` slug for the **new** DP repo that the workflow will create from the template. This repo must not exist yet — the workflow creates it.

- Format: `{org}/{repo-name}`
- Example: `JawaharRamis/sales-products`
- The repo name becomes the domain team's home for all product YAML files

### `repo_pattern` — required

The OIDC audience glob pattern that the `DomainGitHubActionsRole` trust policy uses to restrict which GitHub repos can assume the role.

- Format: OIDC glob matching `repo:{org}/{pattern}:ref:refs/heads/main`
- Example: `JawaharRamis/sales-*` (trusts any repo matching `sales-*` in the org)
- Be as specific as possible — a wildcard like `JawaharRamis/*` is too broad

### Sample filled-in form

```
domain_name:   sales
account_id:    123456789012
aws_region:    ap-southeast-2
owner_email:   jawahar@acme.com
github_repo:   JawaharRamis/sales-products
repo_pattern:  JawaharRamis/sales-*
```

Click **Run workflow**. The workflow targets the `main` branch.

---

## Step 3: Monitor the workflow

The workflow runs four sequential jobs. Each job must pass before the next starts.

### Job 1: `validate`

Duration: ~10 seconds.

Checks:
- `domain_name` matches `^[a-z][a-z0-9_]*$`
- `account_id` is exactly 12 digits
- `owner_email` is a valid email
- `github_repo` is in `org/repo` format
- `repo_pattern` is a non-empty string

If this job fails, fix the input values and re-run. No AWS or GitHub state has been touched.

### Job 2: `provision-iam`

Duration: 2–4 minutes.

Watch for:
- `Assuming MeshOnboardingRole` — OIDC credentials obtained for central account
- `Assuming OrganizationAccountAccessRole` — chained into domain account
- `Creating GitHub OIDC provider` — idempotent; safe if already exists
- `Creating DomainGitHubActionsRole` — the role domain workflows use to deploy products
- `Creating MeshEventRole` — allows EventBridge to forward events to central bus
- `Committing domains/{domain_name}.yaml` — domain registry entry written to main

If this job fails after the commit but before IAM is fully provisioned, the workflow automatically reverts the `domains/` commit. The domain registry is always consistent with what is actually provisioned.

### Job 3: `provision-aws`

Duration: 1–2 minutes.

Steps marked **best-effort**: a failure here does not roll back IAM.

Watch for:
- `Registering Lake Formation S3 location` — registers the gold S3 prefix with LF
- `Creating EventBridge cross-account rule` — allows domain account to forward `datameshy` events to central bus

If either best-effort step fails, note the error but the domain is otherwise functional. You can re-trigger just this job by re-running failed jobs in the Actions UI, or by re-running the full workflow (it is idempotent).

### Job 4: `bootstrap-repo`

Duration: 1–2 minutes.

Watch for:
- `Creating repository from template` — `JawaharRamis/data-meshy-product-template` instantiated as `github_repo`
- `Setting secret AWS_ROLE_ARN` — DomainGitHubActionsRole ARN
- `Setting secret MESH_API_ENDPOINT` — governance API endpoint
- `Setting secret MESH_DOMAIN_NAME` — domain name
- `Opening quickstart issue` — checklist issue #1 in the new DP repo

---

## Step 4: Verify

After all four jobs complete successfully, run through this checklist.

**1. Domain registry committed to main**

Check `domains/{domain_name}.yaml` exists in this repo on main. The file should look like:

```yaml
domain_name: sales
account_id: "123456789012"
aws_region: ap-southeast-2
owner_email: jawahar@acme.com
github_repo: JawaharRamis/sales-products
repo_pattern: JawaharRamis/sales-*
registered_at: "2026-05-15T..."
status: ACTIVE
```

**2. New DP repo created**

Navigate to `https://github.com/{github_repo}` (e.g., `https://github.com/JawaharRamis/sales-products`). The repo should exist with the template structure:

```
products/              (empty — domain team adds products here)
.github/workflows/
  on-push.yml
  deprecate.yml
  rollback.yml
  upgrade-platform.yml
glue_jobs/             (template stubs — domain team customizes)
step_functions/        (template stubs)
```

**3. Quickstart issue opened**

In the new DP repo, check **Issues** — issue #1 should be open with the title `[Quickstart] Set up your first data product` and a checklist for the domain team.

**4. `DomainGitHubActionsRole` exists in domain account**

In the domain's AWS account, navigate to **IAM → Roles** and search for `DomainGitHubActionsRole`. Verify:
- Trust policy references the GitHub OIDC provider
- Trust condition matches `repo:{repo_pattern}:ref:refs/heads/main`
- Permissions include S3, Glue, StepFunctions
- There is an explicit Deny on `lakeformation:GrantPermissions` and `lakeformation:RevokePermissions` — domain roles cannot manage LF grants directly

**5. Catalog record (platform engineers only)**

```
python tools/catalog.py browse --domain sales
```

Expected output shows the domain with `status: ACTIVE` and 0 active products.

---

## Troubleshooting

### `validate` job fails with "domain_name pattern mismatch"

The domain name contains uppercase letters, hyphens, or starts with a digit. Use only lowercase letters, digits, and underscores, starting with a letter.

### `validate` job fails with "account_id must be 12 digits"

Double-check the account ID. Log into the domain AWS account and run `aws sts get-caller-identity` to retrieve the exact account ID.

### `provision-iam` fails at "Assuming OrganizationAccountAccessRole"

The `MeshOnboardingRole` does not have permission to assume `OrganizationAccountAccessRole` in the domain account. This usually means the domain account was not enrolled into the Organization before onboarding, or the role name differs. Verify the account is in the org and that `OrganizationAccountAccessRole` exists in it (it is created automatically by AWS Organizations when accounts are created via the console or CLI).

### `provision-iam` fails after partial IAM creation

The rollback job will revert the `domains/` commit. Check the workflow logs for which IAM resource failed. Common causes:
- IAM role limit reached in the domain account (default 1000) — request a limit increase
- Permission boundary conflict — existing SCPs may block role creation; check org-level SCPs

After the rollback completes, fix the root cause and re-run the full workflow. Re-running is safe because IAM resource creation is idempotent.

### `provision-aws` fails at Lake Formation registration

LF registration can fail if the S3 location is already registered by another role. This is best-effort and does not block the domain. Re-run just the `provision-aws` job after confirming the LF registration state:

```
python tools/catalog.py lf-status --domain sales
```

### `bootstrap-repo` fails at "Creating repository from template"

The `ORG_PAT` secret may have expired or lost `repo` scope. Rotate the token: GitHub → Settings → Developer settings → Personal access tokens → regenerate with `repo`, `admin:org` scopes. Update the `ORG_PAT` secret in this repo's Actions secrets and re-run.

### Re-running the full workflow is safe

`onboard-domain.yml` is idempotent:
- OIDC provider creation is idempotent (AWS returns success if already exists)
- IAM role creation will update the role if it already exists
- `domains/` commit will be skipped if the file already matches
- Repo creation will fail gracefully if the repo already exists and continue to secret-setting

---

## What happens next

Hand off to the domain team:
1. Share the URL of the new DP repo: `https://github.com/{github_repo}`
2. Direct them to issue #1 in that repo (quickstart checklist)
3. Direct them to [Add a Product](ADD-PRODUCT.md) for their first data product

The domain team will never interact with this repo or with Terraform directly. All their work happens through `product.yaml` + `infra.yaml` files in their DP repo.

---

## See Also

- [Add a Product Guide](ADD-PRODUCT.md) — next step for the domain team
- [Subscription Flow](subscription-flow.md) — how consumers subscribe to data products
- [Architecture Document](../../plan/ARCHITECTURE.md) — multi-account architecture and security model
- [`domains/` directory](../../domains/) — all registered domain registry files
