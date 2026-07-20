# Agent and AI Employee Review Entry

## Main Implementation

- Agent base and specialists: `backend/agents/`
- Cluster, roster, routing and runtime context: `backend/orchestrator/agent_*`,
  `cluster_*`, `planner.py`, `brain_router.py`
- Agent management API: `backend/app/agent_management.py`
- Collaboration participants and worker status: `backend/app/collaboration*`
- Prompts and role definitions: `backend/prompts/`
- Desktop management surface: `desktop/.../Features/Agents/`
- Evolution and promotion: `backend/capability/growth/`, review gates and module governance

The repository currently contains specialist Agents for commerce, programming,
game development, video/animation and vision, plus shared base behavior. The
audit should determine which are production execution paths, which are routing
profiles, and which are mostly reserved structure.

## Agent Boundary

An Agent may reason, plan, review and coordinate within governed scope. It
should not silently become the durable queue, canonical workflow engine,
credential store, device driver or model provider. Those responsibilities have
separate contracts.

## AI Employee Questions

- What is the durable identity of an AI Employee across sessions and devices?
- Which Goals, Resources, Policies, Memory and Workflows does it own?
- How are permissions, budgets, approvals and destructive actions governed?
- Can an Agent delegate to another Agent or Worker without bypassing review?
- Are owner-only Agents distinguishable from externally callable Agents?
- Can clients mutate Agent state directly, or only through application APIs?
- How are performance, evaluation, promotion and rollback recorded?
- Is collaboration a messaging layer or a second orchestration system?

## Owner Constraint: AgentA1

`AgentA1` is an owner-private capability and must not be exposed as a public or
tenant-facing Agent. The audit should verify whether the code has an explicit
trust boundary for this rule. This document does not infer AgentA1's internal
capabilities where the source does not define them.

## Initial Classification

- Agent cluster and specialist routing: implemented.
- Desktop Agent management and collaboration surfaces: implemented.
- Long-lived AI Employee ownership model: partially represented across Agent,
  Resource, Context, Memory and governance modules.
- Public/tenant Agent catalog: future direction and constrained by
  `decisions.md`.
