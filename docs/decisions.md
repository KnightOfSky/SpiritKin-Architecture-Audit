# Architecture and Product Decisions

These are owner constraints for architecture review. They are not claims that
the current code fully enforces each decision. Reviewers should identify gaps,
but should not propose changes that reverse these decisions without explicit
owner approval.

## D-001: SpiritKin Core Is Not Rented to Customers

**Status:** Accepted owner decision.

SpiritKin's private core identity, long-term memory, owner context, privileged
Agents and control surface are not a customer-rented SaaS instance. Commercial
products may expose bounded APIs, workflows, workers or domain services, but
they must not expose or clone the owner's private SpiritKin identity.

**Why:** The core combines private memory, credentials, device control,
long-lived context and owner-level authority. Treating it as a tenant product
would collapse the most important trust boundary.

**Architecture consequence:** Multi-tenant work belongs in explicitly isolated
service planes. Owner resources and private Agents remain outside tenant scope.

## D-002: Remote Worker Is Independently Deployable

**Status:** Accepted architecture direction.

A Remote Worker must be able to run without the desktop application and without
implicit access to the owner's local project paths or runtime state.

**Why:** Independent workers enable remote devices, failure isolation, elastic
capacity and clear security boundaries. An HTTP wrapper around local-only
assumptions is insufficient.

**Architecture consequence:** Communication, task state, artifacts, identity,
leases, permissions and recovery must use explicit protocols.

## D-003: AgentA1 Is Not Public

**Status:** Accepted owner decision.

AgentA1 is owner-private and must not be exposed to customers, tenants, public
Agent catalogs or untrusted remote callers.

**Why:** It belongs to the owner trust domain. The exact capabilities may evolve,
but its visibility and authority boundary must remain explicit.

**Architecture consequence:** Agent registration, discovery, routing and API
projection need a reliable owner-only visibility and authorization policy.

## D-004: Model and External Services Use Provider Boundaries

**Status:** Accepted architecture decision.

Model vendors and external services are integrated through Provider contracts
rather than embedded as permanent runtime identities.

**Why:** SpiritKin must support local models, self-hosted services and future
model pools without rewriting Agent, Workflow or client contracts for each
vendor.

**Architecture consequence:** Provider selection, health, metadata, fallback,
credentials and evaluation are explicit runtime data. Provider-specific details
stay behind adapters.

## D-005: Deployment Evolves in Three Stages

**Status:** Accepted direction.

1. API plus independent Remote Worker
2. Self-hosted Model Service
3. Model Pool plus Multi-Tenant infrastructure

Each stage must preserve the prior stage's contracts. Multi-tenancy applies to
bounded service infrastructure, not the private SpiritKin core described in
D-001.

## D-006: Audit Repository Is a Baseline, Not Source of Truth

**Status:** Accepted repository rule.

This repository is a squashed architecture snapshot for review. Development,
fixes and feature work remain in `KnightOfSky/SpiritKinAI`. Findings may cite
this snapshot, but code changes must be reconciled against the current source
repository and its active branch before implementation.
