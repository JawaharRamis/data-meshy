# Guide: Upgrade the Platform Version

> **Phase coverage**: Phase 5 | **Last updated**: 2026-05-15

## Navigation

[<- Docs home](../README.md) | [Product Spec Reference](../reference/PRODUCT-SPEC.md) | [Deprecate a Product](DEPRECATE-PRODUCT.md)

---

## Overview

Each data product repo pins to a specific version of the platform via `platform_version` in `infra.yaml`. This pin controls which version of the Terraform module at `JawaharRamis/data-meshy-product-template` is used when `provision-product.yml` runs.

**Upgrade is pull-based.** The platform team cuts a release and notifies domain teams via a GitHub issue. Domain teams decide when to upgrade. The platform never force-pushes a version bump to domain repos. A product running on an older platform version continues to work as long as that version is not end-of-life.

**Version compatibility.** Each platform release may introduce new `infra.yaml` fields, change Terraform module behavior, or change the reusable workflow logic. The release notes (on the GitHub Release page) describe what changed. Domain teams should read the release notes before upgrading.

---

## For platform engineers: cutting a new release

### Step 1: Prepare the release

1. Merge all intended changes to the `main` branch of `JawaharRamis/data-meshy`.
2. Ensure the corresponding module changes in `JawaharRamis/data-meshy-product-template` are merged and the template repo is tagged with the matching version.
3. Write release notes. Include:
   - New `infra.yaml` fields (with default values if optional)
   - Removed or renamed fields (breaking changes for `infra.yaml`)
   - Changes to `provision-product.yml` behavior
   - Required domain team actions (if any) beyond bumping `platform_version`

### Step 2: Tag and publish the release on GitHub

Create a new GitHub Release on `JawaharRamis/data-meshy`:

```bash
# From data-meshy repo root
gh release create v1.3 \
  --title "Platform v1.3" \
  --notes "$(cat release-notes-v1.3.md)"
```

The tag must follow the format `v{major}.{minor}` (e.g., `v1.3`, `v2.0`). This tag is used as the `platform_version` value in domain `infra.yaml` files and as the `?ref=` in the Terraform module source URL.

### Step 3: notify-upgrade.yml fires automatically

When the GitHub Release is published, `notify-upgrade.yml` triggers automatically. It:

1. Reads all domain registry files in `domains/*.yaml` (skipping `example.yaml`)
2. For each registered domain, reads the `github_repo` field
3. Checks if an issue titled `"Platform {version} available — upgrade when ready"` already exists in that repo (idempotency)
4. If no such issue exists, creates one with the release notes excerpt and upgrade instructions

The issue body instructs the domain team to trigger `upgrade-platform.yml` with `target_version` set to the new release tag.

**Note on GitHub token scope:** `notify-upgrade.yml` uses `GITHUB_TOKEN` by default, which works for same-organisation public repos. For private domain repos, the workflow requires `ORG_PAT` (a Personal Access Token with `repo` scope stored as a repository secret in `data-meshy`). If domain repo issues are not being created, verify that `ORG_PAT` is set. See the workflow header comment for details.

### Step 4: Verify notifications

After the release, check that issues were created in registered domain repos:

```bash
gh issue list --repo {domain-repo} --search "Platform v1.3 available"
```

---

## For domain teams: receiving and acting on upgrade notifications

### Receiving the notification

When the platform team cuts a new release, you will receive a GitHub issue in your DP repo titled:

```
Platform v1.3 available — upgrade when ready
```

The issue body includes:
- A summary of what changed in the new version
- A link to the full release notes
- Instructions for triggering the upgrade

You do not need to upgrade immediately. Your current `platform_version` continues to work until the platform team marks it end-of-life and communicates a migration deadline.

### Reviewing the release notes

Before upgrading, read the full release notes linked in the issue. Pay attention to:

- **New required `infra.yaml` fields**: If the new version adds required fields, the upgrade PR will not include them automatically. You must add them manually before merging.
- **Changed Terraform module behavior**: The new module version may change how Iceberg tables or Glue jobs are configured. Verify there are no unexpected diffs in the PR's Terraform plan output.
- **Workflow file changes**: The upgrade workflow bumps `@v{version}` refs in your workflow stubs. If the new version renames workflow inputs or secrets, you may need to update your workflow files manually.

### Triggering the upgrade

Navigate to your DP repo on GitHub. Go to **Actions** → **Upgrade Platform Version** → **Run workflow**.

| Input | Type | Required | Description |
|---|---|---|---|
| `target_version` | string | Yes | The platform version to upgrade to. Must match the tag on `JawaharRamis/data-meshy` (e.g., `v1.3`). Copy this from the GitHub issue. |

Click **Run workflow**.

### What the upgrade workflow does

`upgrade-platform.yml` in your DP repo (installed at onboarding) runs the following steps:

1. Checks out your DP repo on a new branch named `upgrade/platform-{target_version}`.
2. Finds all `products/*/infra.yaml` files.
3. Updates the `platform_version` field in each `infra.yaml` to `target_version`.
4. Finds all workflow stub files (`.github/workflows/*.yml`) that call reusable workflows from `JawaharRamis/data-meshy`.
5. Updates every `uses: JawaharRamis/data-meshy/.github/workflows/*.yml@v{old}` line to `@{target_version}`.
6. Opens a pull request from the upgrade branch to main with a summary of all files changed.

The PR title follows the format: `chore: upgrade platform to {target_version}`.

### Reviewing the upgrade PR

Before merging the upgrade PR:

1. **Check the Terraform plan in the Actions run.** The `provision-product.yml` reusable workflow runs as a PR check and outputs the Terraform plan. Review it to confirm the changes match what the release notes describe. Unexpected resource replacements should be investigated before merging.
2. **Verify `infra.yaml` changes.** If the new version added new optional fields, they will not appear automatically (the workflow only updates `platform_version`). Add any new fields you want to use manually before merging.
3. **Verify workflow file changes.** Confirm that only `@v{old}` → `@{target_version}` changes were made in workflow files. No structural changes should be present.

When satisfied, merge the PR. The next push to main will use the new platform version.

---

## Version compatibility

### How to check what version you are on

```yaml
# In any products/*/infra.yaml in your DP repo
platform_version: v1.2     # this is your current version
```

### Checking release notes for any version

```bash
gh release view v1.3 --repo JawaharRamis/data-meshy
```

### End-of-life versions

The platform team maintains compatibility for at least two prior minor versions. When a version reaches end-of-life:

1. The platform team opens an issue in all registered DP repos with a migration deadline.
2. After the deadline, the old module version is no longer guaranteed to work (module source may be archived).

Always upgrade within two minor versions of the latest release to stay in the supported window.

---

## Emergency rollback: if the new version breaks provisioning

If you merged the upgrade PR and the new platform version causes `provision-product.yml` to fail (Terraform errors, schema validation failures, or unexpected resource changes):

### Option 1: Revert the upgrade PR (preferred)

1. On GitHub, navigate to the merged upgrade PR.
2. Click **Revert** to create a revert PR.
3. Review the revert PR (it should restore the old `platform_version` and `@v{old}` workflow refs).
4. Merge the revert PR. The next `provision-product.yml` run uses the previous version.

### Option 2: Manual version bump

If the PR revert is not available, manually edit `infra.yaml` in each product directory:

```yaml
# Roll back to the working version
platform_version: v1.2    # was v1.3
```

Also revert the `@v{version}` refs in your workflow stub files. Open a PR, get it reviewed, and merge.

### Reporting the issue

File a bug in `JawaharRamis/data-meshy` with:
- The `target_version` you upgraded to
- The `provision-product.yml` Actions run URL showing the failure
- The Terraform error output

The platform team will investigate and, if needed, cut a patch release.

---

## Troubleshooting

| Problem | Cause | Resolution |
|---|---|---|
| No upgrade issue received in DP repo | `notify-upgrade.yml` used `GITHUB_TOKEN` but your DP repo is private | Platform team must configure `ORG_PAT`. Contact platform team. |
| `upgrade-platform.yml` not found in DP repo | DP repo was onboarded before Phase 5 or workflow file missing | Contact platform team to apply the updated template. |
| Upgrade PR Terraform plan shows unexpected resource replacement | New module version changed a resource that forces replacement | Read release notes carefully. If unintended, file a bug. Do not merge until resolved. |
| `infra.yaml` still shows old `platform_version` after upgrade PR | Upgrade workflow failed to find `infra.yaml` files | Check that products are in `products/*/infra.yaml` (not a subdirectory). Check the workflow run logs. |
| `provision-product.yml` fails after upgrade with `module not found` | `target_version` tag does not exist in `data-meshy-product-template` | Verify the tag exists: `gh release list --repo JawaharRamis/data-meshy-product-template`. Contact platform team if the tag is missing. |

---

## See Also

- [Product Spec Reference](../reference/PRODUCT-SPEC.md) — `platform_version` in `infra.yaml` field reference
- [ADR-012: Platform-managed onboarding](../decisions/ADR-012-platform-managed-onboarding.md) — how upgrades fit the overall platform model
- [ADR-011: No HCL in DP repos](../decisions/ADR-011-no-hcl-in-dp-repos.md) — why `platform_version` is the only version management domain teams do
- `notify-upgrade.yml` — platform workflow that creates upgrade issues
- `data-meshy-product-template` — GitHub repository containing the versioned Terraform modules
