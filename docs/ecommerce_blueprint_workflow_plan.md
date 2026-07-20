# Ecommerce Blueprint Workflow Plan

Date: 2026-06-08

## Layering

The ecommerce automation stack follows a Blueprint-style split:

| Layer | Responsibility | Example |
| --- | --- | --- |
| WorkflowDefinition | Business graph definition and node dependencies | `ecommerce.auto_listing.v1` |
| WorkflowRun | One product/batch runtime instance | `wfr_*` |
| WorkflowNode | A graph node with status, inputs, outputs, artifacts | `productdata_build` |
| Skill | Auditable reusable capability used by nodes | `ecommerce.pdd_mobile_link_intake.workflow` |
| Tool | Native executable operation registered in ToolRegistry | `ecommerce.task_queue.ingest_mobile_links` |
| Agent | Worker/owner for human-like reasoning or domain execution | `ecommerce`, `vision_model` |

Workflow owns process state. Skill owns reusable action definitions. Tool owns
native execution. Agent owns reasoning/claiming work.

## Current Implementation

Implemented:

- Native ecommerce task queue core: `backend/orchestrator/ecommerce_task_queue.py`
- Native ToolRegistry tools:
  - `ecommerce.task_queue.status`
  - `ecommerce.task_queue.ingest_mobile_links`
  - `ecommerce.task_queue.enqueue_image`
  - `ecommerce.task_queue.attach_probe`
  - `ecommerce.task_queue.attach_productdata`
  - `ecommerce.task_queue.cleanup_temp`
- Skill import script: `scripts/import_ecommerce_rpa_skills.py`
- Minimal Blueprint-style model/runner: `backend/orchestrator/workflow_graph.py`
- JSON workflow store: `backend/orchestrator/workflow_store.py`
- Desktop workflow management endpoint: `backend/app/workflow_management.py`
- Candidate workflow definition: `build_ecommerce_auto_listing_definition()`
- Workflow ToolRegistry tools:
  - `workflow.graph.save_ecommerce_definition`
  - `workflow.graph.list_definitions`
  - `workflow.graph.start_run`
  - `workflow.graph.list_runs`
  - `workflow.graph.run_next`
  - `workflow.graph.run_node`
  - `workflow.graph.approve_review`
  - `workflow.graph.claim_agent_task`
  - `workflow.graph.complete_agent_task`
- Desktop console panel: `frontend/desktop_console.html`, tab `工作流`

Imported Skill candidates:

- `ecommerce.pdd_mobile_link_intake.workflow`
- `ecommerce.browser_extension_productdata.workflow`
- `ecommerce.ocr_artifact_cleanup.workflow`

All three imported ecommerce Skill candidates use project-native structured
tools. They do not call AutoProcess or the external RPA workspace at runtime.

## Workflow Skeleton

`ecommerce.auto_listing.v1`:

1. `product_selection`: agent task, ecommerce Agent
2. `source_capture`: agent task, vision/RPA Agent
3. `mobile_link_intake`: tool call, `ecommerce.task_queue.ingest_mobile_links`
4. `productdata_build`: skill call, `ecommerce.browser_extension_productdata.workflow`
5. `listing_gate`: review gate, `core_review`
6. `listing_draft`: agent task, ecommerce Agent
7. `publish_review`: review gate, `human_review`
8. `publish_or_hold`: agent task, ecommerce Agent

This is not a visual editor yet. It is the stable data model and runtime
surface that a UE5-style node editor can render later.

## Runtime Semantics

Workflow is the process graph. Skill is a reusable auditable capability called
by a node. Tool is the native execution primitive. Agent task nodes are not
auto-executed by `WorkflowRunner`; an Agent or scheduler must claim the node
through `workflow.graph.claim_agent_task` and submit results through
`workflow.graph.complete_agent_task`.

The current workflow tools can persist definitions/runs, dry-run nodes, execute
tool/skill nodes, approve review gates, and record Agent task completion.
The desktop console now exposes the same state through `/desktop/workflows`.

Tenant self-service consoles must not call `workflow.graph.*` actions yet.
`account_console` tokens are explicitly blocked from this tool surface because
the current Blueprint runner does not pass through the cloud control plane's
account quota, scrape metering, worker dispatch, and audit gates. Tenant access
can be opened only after a Blueprint-to-control-plane bridge routes graph node
execution through the same metered worker task path used by the SaaS foundation.

## Next Hard Gaps

The main runtime now has queue, artifact lifecycle, and productData adapter
tools. The next gaps are:

- Upgrade the current workflow panel into a drag-and-drop Blueprint editor.
- Wire scheduler/UI entrypoints so Agent/SkillRunner can dry-run, review,
  claim, and execute workflow nodes from the same state model.
