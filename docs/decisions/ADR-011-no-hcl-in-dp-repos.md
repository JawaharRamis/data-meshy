# ADR-011: No HCL in data product repos

> **Phase coverage**: Phase 4 refactor | **Last updated**: 2026-05-15

## Navigation
← [Decisions](../README.md) | [↑ Docs home](../../README.md)

## Status
Accepted

## Context

The previous architecture scaffolded domain repos (via `datameshy domain init`) with a `infra/` directory containing Terraform HCL that called platform modules. Domain teams owned that HCL and ran `terraform apply` in their CI.

This created several problems:

- **Wrong audience**: Domain teams are data engineers and product owners, not platform engineers. HCL introduces Terraform state management, backend configuration, module pinning, and plan/apply semantics as required knowledge for anyone publishing a data product.
- **Drift risk**: Once HCL lives in a DP repo, domain teams can modify it — adding resources, changing module inputs, or upgrading modules independently. This silently diverges from the platform's required shape (Iceberg table location conventions, LF-Tag requirements, EventBridge source format). Diverged DP repos break catalog registration and subscription provisioning in ways that are hard to diagnose.
- **Terraform state in domain accounts**: Each DP repo needed its own S3 backend for Terraform state, requiring platform setup per repo and creating state management overhead for domain teams.
- **Version control complexity**: Module version pinning lived in `main.tf` in the DP repo. Upgrades required the domain team to understand what changed in the platform module, edit HCL, run `terraform plan`, and verify outputs — a meaningful skill requirement.

Alternatives considered:

- **Thin `main.tf` calling a platform module**: Reduces HCL surface area to one file and one `module` block. Still requires Terraform knowledge for debugging. Domain teams can still drift by adding `resource` blocks or changing inputs.
- **Terraform CDK in Python**: Familiar language for data engineers but introduces a new toolchain and the same state management overhead.
- **Platform provisions all resources centrally (no domain-account Terraform)**: Removes domain team control entirely. Violates federated governance — domain teams should own their domain infrastructure.

## Decision

Data product repos contain no HCL. All Terraform is owned and executed exclusively by the platform's GitHub Actions reusable workflows.

**What domain teams author:** `product.yaml` and `infra.yaml` (see ADR-010). That is the complete surface area.

**What the platform executes on their behalf:**
```
DP repo commit triggers:
  uses: JawaharRamis/data-meshy/.github/workflows/provision-product.yml@v1.2

  Platform workflow:
    reads product.yaml  → calls catalog registration API
    reads infra.yaml    → converts fields to Terraform -var inputs
    runs terraform apply (platform module, pinned to platform_version)
    against domain account via OIDC (DomainGitHubActionsRole)
```

The version pin (`@v1.2` on the reusable workflow) is the only version management domain teams do — and it is a one-line change in a YAML file, not a Terraform refactor.

**Terraform state** is managed by the platform: a single S3 backend in the central governance account, keyed by `{domain}/{product_name}`. Domain teams never interact with Terraform state directly.

**Enforcement:** The DP repo template (instantiated at onboarding) contains no `.tf` files. If a domain team adds HCL, it is ignored — the reusable workflow does not run `terraform init` against any files in the DP repo. The platform module is fetched directly from `data-meshy` at the pinned ref.

## Consequences

### Positive
- **Zero Terraform knowledge required**: Domain teams publish data products by authoring YAML. No `terraform init`, `plan`, `apply`, state management, or backend configuration.
- **Drift prevention**: Platform controls the Terraform execution environment. Domain teams cannot add resources or change module inputs beyond what `infra.yaml` exposes.
- **Centralised state**: One Terraform state store (central account S3) covers all domain products. No per-repo backend setup.
- **Upgrade path is a YAML line**: Bumping `platform_version: v1.2 → v1.3` in `infra.yaml` is the entire upgrade. The reusable workflow picks up the new module version automatically.

### Negative
- **Reduced flexibility**: Domain teams cannot customise infrastructure beyond what `infra.yaml` exposes. Adding a new Glue job type or a non-standard S3 layout requires a platform module change and a new `infra.yaml` field, not a local `main.tf` edit. This is intentional — the platform defines the contract — but it is a real constraint.
- **Platform becomes a bottleneck for infra extensions**: If a domain team needs an infra option not yet in `infra.yaml`, they open a PR to `data-meshy`. Mitigated by keeping `infra.yaml` fields broad enough to cover standard cases and by maintaining a fast PR review SLA for extension requests.
- **Debugging requires platform involvement**: If `terraform apply` fails in the reusable workflow, the domain team sees an Actions failure but cannot inspect Terraform state or run `plan` locally. The platform team must investigate. Mitigated by structured error output in the reusable workflow and clear runbook in `data-meshy` docs.

## See also
- [ADR-010](./ADR-010-two-yaml-interface.md) — the two-YAML interface that replaces HCL inputs
- [ADR-012](./ADR-012-platform-managed-onboarding.md) — how DomainGitHubActionsRole is provisioned at onboarding
- [ADR-002](./ADR-002-decomposed-iam.md) — IAM role model; DomainGitHubActionsRole extends this
