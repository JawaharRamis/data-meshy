# Changelog

> Auto-maintained by Claude Code. Newest entries first.

## [2026-05-25] phase-6-verification-and-docs

**Type**: fix + docs
**Summary**: Completed Phase 6 end-to-end: smoke-tested all deployed Lambda functions (fixing 2 runtime bugs found during invocation), resolved 3 CI blockers in the Terraform Plan workflow, replaced the infra-scoped BOOTSTRAP.md with a comprehensive platform-wide bootstrap guide at `docs/PLATFORM-BOOTSTRAP.md`.
**Decisions**: `catalog_writer` archive must bundle `event_validator.py` alongside it (single-file zip caused ImportModuleError). `audit_writer` env var corrected from `MESH_AUDIT_TABLE` → `MESH_AUDIT_LOG_TABLE`. `org_id` variable validation cannot cross-reference `var.organizations_enabled` in Terraform — replaced with format regex. `infra/environments/central/BOOTSTRAP.md` deleted as superseded by the new comprehensive guide. `ORG_PAT` secret added to repo, `AWS_ACCOUNT_ID` secret added to repo (was causing OIDC role ARN to be constructed as `arn:aws:iam:::role/...`).
**Files Changed**: lambdas/audit_writer.py, infra/modules/governance/lambdas.tf (catalog_writer archive bundle), infra/environments/central/main.tf (org_id validation), infra/environments/central/BOOTSTRAP.md (deleted), docs/PLATFORM-BOOTSTRAP.md (NEW — 11-section bootstrap guide, 857 lines)
**Status**: complete — all merged to main (PRs #48, #50, #51)
**Next Steps**: Issue #43 — onboard sales domain via `onboard-domain.yml` (requires real sales AWS account ID). Issues #44 and #45 remain blocked behind #43.

## [2026-05-17] phase-6-live-deploy

**Type**: infra
**Summary**: First live `terraform apply` of the central governance stack. All 23 orphaned IAM roles, EventBridge resources, and log groups imported into state after a partial first run left them untracked. DynamoDB tables, S3 platform-modules bucket, and EventBridge bus still need import — apply in progress. Also created `docs/guides/DEPLOY-AND-PROVE.md` (8-phase end-to-end deployment guide) and answered why the first apply must be manual (GitHub OIDC provider and TerraformApplyRole don't exist until after first apply — bootstrap chicken-and-egg).
**Decisions**: Partial backend config pattern adopted (`backend.tf` omits bucket, passed via `-backend-config=backend.tfbackend` — file is gitignored so account ID never enters version control). KMS key policy missing `logs.amazonaws.com` and `sns.amazonaws.com` service principals — fixed in `infra/modules/governance/main.tf`. SNS tags cannot contain apostrophes — fixed tag value. BOOTSTRAP.md step 9 added: first apply is manual-only with explanation.
**Files Changed**: docs/guides/DEPLOY-AND-PROVE.md (NEW), infra/modules/governance/main.tf (KMS + SNS fixes), infra/environments/central/BOOTSTRAP.md (step 9 added), infra/environments/central/backend.tf (bucket removed), infra/environments/central/backend.tfbackend.example (NEW), .gitignore (backend.tfbackend added)
**Status**: in-progress — terraform apply blocked on remaining orphaned resources (DynamoDB tables, S3 bucket, EventBridge bus, DataZone role)
**Next Steps**: Import remaining orphaned resources and complete apply. Then capture outputs (api_endpoint_url, terraform_apply_role_arn) for GitHub secrets.

## [2026-05-17] phase-6-stream-2-merged

**Type**: infra
**Summary**: Phase 6 Stream 2 PR #47 merged. Terraform blockers resolved: Organizations-dependent resources guarded with `count = var.organizations_enabled ? 1 : 0`, backend.tf moved to partial config pattern (account ID removed), and bootstrap documentation updated to use `-backend-config` flag.
**Decisions**: (none new — implementation of already-approved stream plan)
**Files Changed**: infra/environments/central/backend.tf, infra/environments/central/BOOTSTRAP.md, .gitignore
**Status**: complete — merged to main (c262a02)
**Next Steps**: Run terraform apply to deploy the central governance stack.

## [2026-05-15] docs-phase-4-5-update

**Type**: docs
**Summary**: Rewrote all platform documentation to reflect the Phase 4-5 architecture. Removed all stale Phase 1 content (pip install datameshy, local Terraform in domain repos, HCL in DP repos). Every guide now accurately describes the two-YAML interface, workflow-driven provisioning, and the template repo model.
**Decisions**: Added `infra.yaml` field reference to PRODUCT-SPEC.md (it had no reference doc). Created two new guides — DEPRECATE-PRODUCT.md and UPGRADE-PLATFORM.md — to cover lifecycle operations that had no docs. Kept DataZone UI section removed from subscription-flow.md (superseded by request-subscription.yml approval gate).
**Files Changed**: docs/README.md, docs/architecture/OVERVIEW.md, docs/components/DATA-PRODUCT.md, docs/guides/* (5 rewrites + 2 new: DEPRECATE-PRODUCT.md, UPGRADE-PLATFORM.md), docs/reference/PRODUCT-SPEC.md
**Status**: complete — merged to main (fb3bf0f)
**Next Steps**: Run `/triage` to pick up any new issues. Consider extracting ADR-003 through ADR-009 from ARCHITECTURE.md into separate files to complete the decisions/ directory.

## [2026-05-15] MILESTONE: phase-5-platform-wiring-complete

**Type**: feat
**Summary**: Phase 5 Platform Wiring complete — all 4 streams merged (PRs #34–#37). Domain teams now interact exclusively via YAML files and GitHub Actions: no pip install, no local Terraform. The refactor from a monorepo pip CLI to a distributed GitHub-Actions-first platform is done.
**Decisions**: `notify-upgrade.yml` uses `GITHUB_TOKEN` for cross-repo issue creation — works for same-org repos but requires `ORG_PAT` for private domain repos (known limitation, documented in workflow header). `tools/catalog.py` uses DynamoDB scan + FilterExpression for domain queries (no GSI needed for a platform-internal tool with low call volume). `subscription_saga.asl.json` kept in data-meshy (referenced by `infra/modules/subscription/step_functions.tf`) — not moved to template repo. ADR-011 historical reference to `datameshy domain init` retained in docs — describes superseded approach.
**Files Changed**: .github/workflows/* (6 new: provision-product.yml, onboard-domain.yml, notify-upgrade.yml, request-subscription.yml, lambda-test.yml, validate-domains.yml), infra/modules/governance/iam.tf (+MeshOnboardingRole), tools/catalog.py (NEW), lambdas/requirements.txt (NEW), schemas/domain.json (NEW), domains/example.yaml (NEW), infra/modules/data-product/* (deleted 8 files), infra/modules/domain-account/* (deleted 7 files), cli/* (deleted 36 files), templates/glue_jobs/* (deleted 5 files), templates/step_functions/medallion_pipeline.asl.json (deleted), docs/* (17 files updated), examples/* (6 READMEs updated)
**Status**: complete — all PRs merged to main
**Next Steps**: Human actions required: (1) terraform apply for MeshOnboardingRole in governance account, (2) add ORG_PAT secret to data-meshy repo, (3) enable Template Repository in data-meshy-product-template Settings, (4) create subscription-approval GitHub Environment. Then deploy and smoke-test onboard-domain.yml end-to-end.

## [2026-05-15] phase-4-platform-actions-complete

**Type**: feat
**Summary**: Phase 4 GitHub Actions Foundation complete — 3 streams merged (PRs #31–#33). Replaced all pip CLI calls in CI with inline Python/shell. Added domain registry (`domains/*.yaml`, `schemas/domain.json`, `validate-domains.yml`). Created reusable workflows for the full product lifecycle: provision, deprecate, rollback. Also cloned `data-meshy-product-template` locally and set up full development workflow infrastructure (skills, hooks, CLAUDE.md, triage labels).
**Decisions**: `reusable-product-validate.yml` replaced `pip install datameshy` with inline jsonschema validation against `schemas/product_spec.json`. `provision-product.yml` Terraform steps guarded with `if: false` until template repo modules are in place (removed in Phase 5). `deprecate-product.yml` and `rollback-product.yml` hardcode `aws-region: us-east-1` (noted for Phase 5 fix). OIDC trust scoped to `ref:refs/heads/main` only (not wildcard) following security review.
**Files Changed**: .github/workflows/reusable-product-validate.yml, .github/workflows/validate-domains.yml (NEW), .github/workflows/provision-product.yml (NEW), .github/workflows/deprecate-product.yml (NEW), .github/workflows/rollback-product.yml (NEW), schemas/domain.json (NEW), domains/example.yaml (NEW), docs/decisions/ADR-010/011/012.md (NEW)
**Status**: complete
**Next Steps**: Phase 5 complete (see entry above). Pending human infrastructure actions before live onboarding.

## [2026-05-14] phase-3-stream-3-versioned-examples

**Type**: feat
**Summary**: Phase 3 Stream 3 implemented. Added three reference example data products demonstrating ADR-009 versioned coexistence: `campaign_metrics` (marketing, schema v1), `revenue_daily_v1` (sales, deprecated with sunset_date 2026-09-01), and `revenue_daily_v2` (sales, schema v2 breaking change — `revenue` renamed to `gross_revenue`, `net_revenue` added). Products include Glue job stubs and READMEs explaining the migration story.
**Decisions**: Directory named `revenue_daily_v1/` (not `revenue_daily/`) so the directory key matches the catalog product name. `deprecated`, `sunset_date`, `superseded_by` added as top-level product.yaml fields — safe because `additionalProperties: true` in JSON schema; these are spec-level documentation, authoritative state lives in DynamoDB. Integration catalog search ACs require live DynamoDB — documented in READMEs, not unit-testable locally.
**Files Changed**: examples/marketing-domain/* (6 files), examples/sales-domain/products/revenue_daily_v1/* (5 files), examples/sales-domain/products/revenue_daily_v2/* (5 files), examples/sales-domain/README.md, examples/marketing-domain/README.md
**Status**: in-progress — PR #19 open, closes #16
**Next Steps**: Merge PR #19 after CI passes. Then Phase 3 is complete — run `/changelog --milestone` and begin Phase 4 (Governance & Trust) planning.

## [2026-05-03] phase-3-streams-1-2-complete

**Type**: feat
**Summary**: Phase 3 Streams 1 and 2 merged (PRs #17, #18). Catalog discovery CLI (`catalog search/browse/describe`) added with DynamoDB GSI-backed Lambda handlers and new API Gateway routes. Product lifecycle commands (`product deprecate`, `product rollback`, `product import`) added with retirement Lambda, Iceberg rollback Glue job, and EventBridge Scheduler integration. 128 unit tests across both streams. Security reviews completed; 4 CRITICAL/HIGH findings fixed.
**Decisions**: Keyword search uses per-domain GSI3 queries + Python-side filter to avoid table scan (OpenSearch migration path at >100k products per ADR-006). EventBridge Scheduler rule for retirement created dynamically by CLI at deprecation time (not Terraform) because sunset_date varies per product. `--list-snapshots` outputs Athena SQL hint rather than executing. Cross-stream concern: `subscribe.py` does not proactively reject subscriptions to DEPRECATED/RETIRED products at CLI layer (server-side enforcement exists via retirement Lambda).
**Files Changed**: cli/datameshy/commands/catalog.py (NEW), cli/datameshy/commands/product.py (extended), lambdas/catalog_search.py, lambdas/catalog_browse.py, lambdas/catalog_describe.py (NEW), lambdas/product_deprecation.py (NEW), lambdas/retirement.py (NEW), templates/glue_jobs/iceberg_rollback.py (NEW), infra/modules/governance/api_gateway.tf, infra/modules/data-product/lifecycle.tf (NEW), cli/tests/* (5 new test files, 91+37 tests), lambdas/tests/* (4 new test files)
**Status**: complete — PRs #17 and #18 merged to main
**Next Steps**: Stream 3 versioned examples (issue #16)

## [2026-05-02] MILESTONE: pre-phase-3-complete

**Type**: feat
**Summary**: Pre-Phase 3 multi-repo architecture groundwork complete. Three parallel streams merged (PRs #9, #10, #11): `datameshy domain init` scaffolds isolated domain repos from Jinja2 templates; `domain upgrade` bumps pinned platform version across all files; reusable GitHub Actions workflows extracted so domain repos call canonical CI steps; legacy mono-repo examples archived and docs updated for multi-repo flow.
**Decisions**: Domain repo scaffold uses `git::` module sources pinned to `?ref=vX.Y.Z` (not S3 sources yet — S3 registry is infra-ready but unused). OIDC apply role restricted to `ref:refs/heads/main` for both platform and domain repos (wildcard was a HIGH finding from code review). `Environment` object is a singleton per `domain init` call (was per-file). Dead `[tool.setuptools.package-data]` removed — build backend is hatchling only.
**Files Changed**: cli/datameshy/commands/domain.py, cli/tests/test_domain_init.py (29 tests), cli/pyproject.toml, cli/datameshy/templates/domain_repo/* (10 templates), .github/workflows/* (7 files), infra/environments/central/platform-registry.tf, infra/modules/governance/iam.tf, examples/example-domain-repo/* (12 files), docs/guides/* (5 files), docs/architecture/* (2 files), docs/reference/TERRAFORM-MODULES.md
**Status**: complete
**Next Steps**: Deploy platform-registry S3 bucket to central account. Smoke-test `datameshy domain init` end-to-end. Begin Phase 3 (Scale) planning — 3-5 domains, discovery catalog.

## [2026-04-20] MILESTONE: phase-2-complete

**Type**: feat
**Summary**: Phase 2 Sharing complete. All 4 streams (infra, lambdas, CLI, examples) implemented, tested (82 unit tests), and merged to main. Marketing domain can now subscribe to Sales data products with cross-account Lake Formation grants, KMS access, and Glue resource links. Both CLI and DataZone UI subscription flows working end-to-end.
**Decisions**: Subscription saga uses three-step provisioning (LF grant → KMS grant → resource link) with compensating rollback on failure. Column-level PII filtering enforced at LF layer. SigV4-signed API requests for governance roles. ASL template conflict resolved by keeping full Stream 2 implementation.
**Files Changed**: infra/modules/subscription/* (5 files), infra/modules/governance/* (3 files), infra/environments/domain-marketing/* (4 files), lambdas/subscription_*.py (4 files), lambdas/tests/* (4 files), cli/datameshy/commands/subscribe.py, cli/datameshy/lib/aws_client.py, examples/marketing-domain/* (3 files), schemas/events/SubscriptionRevoked.json, .gitignore
**Status**: complete
**Next Steps**: Deploy Phase 2 infra to AWS accounts. Execute integration tests per examples/marketing-domain/README.md (CLI flow, DataZone portal flow, failure/compensation scenarios). Begin Phase 3 (Scale) planning.

## [2026-03-30] architecture-review

**Type**: design
**Summary**: Staff-architect-level critique of PRD + ARCHITECTURE.md. Evaluated against data mesh principles, AWS best practices, and enterprise production readiness.
**Decisions**: Identified 3 critical tensions (medallion model contradicts domain autonomy, central catalog creates hub-and-spoke bottleneck, LF cross-account complexity underestimated). Recommended separating platform contract from internal implementation.
**Files Changed**: (none — design review session)
**Status**: complete
**Next Steps**: Address medallion-vs-mesh tension before Phase 1; move Iceberg maintenance and basic observability to Phase 1; add minimal web UI by Phase 2

## [2026-03-30] project-init

**Type**: infra
**Summary**: Initialized project repo, created remote on GitHub, configured .gitignore for local-only files (.claude/, plan/, CLAUDE.md).
**Decisions**: Added .claude/, plan/, and CLAUDE.md to .gitignore to keep local tooling state out of version control.
**Files Changed**: .gitignore
**Status**: complete
**Next Steps**: Begin Phase 1 Foundation implementation after architecture review is incorporated
