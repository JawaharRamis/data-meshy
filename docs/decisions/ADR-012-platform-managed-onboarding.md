# ADR-012: Platform-managed domain onboarding via workflow_dispatch

> **Phase coverage**: Phase 4 refactor | **Last updated**: 2026-05-15

## Navigation
← [Decisions](../README.md) | [↑ Docs home](../../README.md)

## Status
Accepted

## Context

Before a domain team can publish any data products, the platform must establish cross-account trust:

- OIDC trust in the domain account (so GitHub Actions can assume roles without static credentials)
- Scoped IAM roles in the domain account (`DomainGitHubActionsRole` for provisioning, `MeshEventRole` for EventBridge)
- Lake Formation location registration
- EventBridge cross-account rule (domain → central bus)
- A DP repo instantiated from the platform template with correct workflow files and GitHub secrets injected

This setup requires permissions that only the platform team holds: central governance account credentials and the ability to provision IAM in domain accounts via AWS Organizations. Domain teams cannot self-serve this because they do not have cross-account IAM provisioning rights, and accepting domain-team-provided credentials for this step is a significant attack surface.

Alternatives considered:

- **Domain team opens a PR to data-meshy with `domains/{name}.yaml`**: Provides a visible, reviewable record but the PR is authored by the domain team and reviewed by platform. Rejected because the domain registry entry should be created by the platform after successful provisioning, not before. A failed provisioning run would leave a registry entry pointing at a broken setup.
- **Self-service via domain team triggering a workflow in their own repo**: Requires giving domain teams platform credentials before onboarding is complete — circular dependency. Rejected.
- **Platform engineer runs Terraform locally**: Bypasses the GitHub Actions audit trail. Any local apply is invisible to the audit log and cannot be reproduced from CI. Rejected.
- **Fully automated (triggered by any domain team member via GitHub Actions)**: Requires platform credentials to be broadly accessible. OIDC subject conditions on `MeshOnboardingRole` can restrict to specific repos and branches, but the blast radius of a compromised triggering mechanism is domain-account IAM provisioning. Gated by platform team approval.

## Decision

Domain onboarding is a one-time, platform-team-triggered ceremony via `workflow_dispatch: onboard-domain.yml` in the `data-meshy` repository.

**Inputs (provided by platform team at trigger time):**
```yaml
domain_name:    sales
account_id:     "123456789012"
aws_region:     ap-southeast-2
owner_email:    jawahar@acme.com
github_repo:    acme-data/sales-products   # target DP repo to create
repo_pattern:   "sales-*"                  # OIDC subject condition for domain account
```

**What the workflow provisions (in order):**

1. **Central account**: Adds `domains/sales.yaml` to the data-meshy registry
2. **Domain account** (via `MeshOnboardingRole`):
   - GitHub OIDC identity provider (if not already present)
   - `DomainGitHubActionsRole`: trusted by GitHub OIDC for `repo:acme-data/sales-*:ref:refs/heads/main`. Permissions: S3/Glue/Step Functions/Iceberg within domain account. Explicit deny on LF permission management (per ADR-002 principle).
   - `MeshEventRole`: EventBridge `PutEvents` to central bus, scoped to domain event sources
   - Lake Formation location registration for domain S3 buckets
   - EventBridge cross-account rule (domain bus → central bus)
3. **GitHub** (via `GITHUB_TOKEN` with org-level repo creation permission):
   - Instantiates `data-meshy-product-template` into the target repo
   - Injects three secrets into the new repo: `AWS_ROLE_ARN` (DomainGitHubActionsRole ARN), `MESH_API_ENDPOINT` (central API Gateway URL), `MESH_DOMAIN_NAME` (domain identifier)
4. **Notification**: Opens a GitHub issue in the new DP repo confirming onboarding is complete, linking to the quickstart guide

**Two new IAM roles introduced (extending ADR-002):**

| Role | Account | Trust | Permissions |
|------|---------|-------|-------------|
| `MeshOnboardingRole` | Central governance | GitHub Actions OIDC, restricted to `data-meshy` repo main branch only | `sts:AssumeRole` into domain accounts via Organizations, IAM provisioning in domain accounts scoped to `MeshEventRole` and `DomainGitHubActionsRole` only. Cannot provision other resource types. |
| `DomainGitHubActionsRole` | Domain account (one per domain) | GitHub Actions OIDC, restricted to the domain's DP repo pattern and main branch | S3/Glue/Step Functions/Secrets within domain. Explicit deny on LF permission management. Permission boundary restricts to own domain resources (per ADR-002 principle). |

**The domain registry** (`domains/*.yaml` in `data-meshy`) is the authoritative list of provisioned domains. The update notification workflow reads this registry to know which repos to notify on new platform releases.

**Upgrades**: When the platform cuts a new release, `notify-upgrade.yml` reads `domains/*.yaml` and opens a notification issue in each registered DP repo. Domain teams trigger `upgrade-platform.yml` in their repo when ready — it bumps the `@ref` in their workflow files and `platform_version` in `infra.yaml`, opening a PR for their own review before merging. The platform never pushes directly to DP repos after onboarding.

## Consequences

### Positive
- **Full audit trail**: Every onboarding is a GitHub Actions run — who triggered it, when, what was provisioned. Reproducible and reviewable.
- **No static credentials**: Domain account access uses OIDC throughout. The `DomainGitHubActionsRole` is scoped to the specific DP repo and main branch. No long-lived credentials exist.
- **Guaranteed DP repo structure**: Template instantiation ensures every DP repo starts with the correct workflow files, directory layout, and secrets. Domain teams cannot start from a blank slate and miss required files.
- **Domain team control after onboarding**: Once provisioned, the DP repo operates autonomously. Platform does not need write access to DP repos after onboarding completes.
- **Upgrade is pull-based**: Domain teams control when they upgrade. Platform notifies but never forces.

### Negative
- **Platform team gated**: Onboarding requires platform team involvement. Not self-service. Acceptable because onboarding is rare (once per domain) and the gate is a governance requirement — not all domain teams should be able to join the mesh unilaterally.
- **Org-level GitHub permissions required**: The `onboard-domain.yml` workflow needs `repo:create` and `secrets:write` permissions in the GitHub org. This is scoped to the platform team's GitHub token, not broadly distributed, but it is a broad permission that must be carefully protected.
- **Template drift**: DP repos instantiated from the template at different points in time may have different base workflow files. The upgrade mechanism handles this for reusable workflow refs but not for structural template changes (new files, directory renames). Breaking template changes require a migration guide.

## See also
- [ADR-002](./ADR-002-decomposed-iam.md) — decomposed IAM model; MeshOnboardingRole and DomainGitHubActionsRole extend the role inventory
- [ADR-011](./ADR-011-no-hcl-in-dp-repos.md) — DomainGitHubActionsRole is the credential that executes Terraform on behalf of DP repos
- [ADR-010](./ADR-010-two-yaml-interface.md) — product.yaml and infra.yaml are the files domain teams author after onboarding
- [Domain registry](../../domains/) — authoritative list of provisioned domains
- [Onboarding workflow](../../.github/workflows/onboard-domain.yml) — implementation
