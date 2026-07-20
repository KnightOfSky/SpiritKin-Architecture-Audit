# Workflow Canvas And Multi-Agent Reference Notes

Last updated: 2026-06-10

## Immediate Canvas Diagnosis

The workflow nodes stacked after a normal click because older workflow definitions do not always carry `metadata.position`.

The initial graph renderer used indexed fallback positions, so the first render looked correct. The selection path then rebuilt the editor model from JSON, but the editor fallback returned the same default position for every node. A plain click therefore reserialized the graph with all nodes at the same coordinate. Selection, Ctrl multi-select, and rubber-band selection should update selected state only; they should not rebuild the workflow definition unless the user actually edits node data.

Applied fixes:

- Missing-position fallback is now index-based in both graph and edit-node view models.
- Selection paths re-render the graph from the active definition instead of calling the editor serialization path.
- Rubber-band selection no longer rebuilds the definition.
- Canvas zoom now uses a scaled render surface with a matching scroll host size, avoiding the heavier `LayoutTransform` style of remeasure.
- Edges now connect to the rectangle boundary in the direction of the target node instead of always right-edge to left-edge.
- Swimlanes are now semantic constraints, not decorative bands. `agent_task` stays in the Agent lane, `tool_call` and `skill_call` stay in the Tool/Skills lane, `review_gate` stays in the Review lane, and unknown/future node types stay in the Extension lane. Dragging adjusts horizontal position freely but clamps vertical position to the node type's lane.
- Node operations now live on the canvas: right-click a node to edit, apply inspector values, set it as a connection source, add downstream Agent/Tool/Skill/Gate nodes, duplicate it, disconnect inputs/outputs, or delete it. Right-click empty canvas for root-node creation and workflow-level actions.
- The right side of the Workflows page is now a workflow Inspector for definition-level actions: save definition, start run, validate, auto-layout, duplicate definition, and delete definition. The node form remains an inspector for the selected node's properties and arguments, not the primary node CRUD surface.
- Edges now anchor to visible execution ports: `exec_out` on the source node to `exec_in` on the target node. Saved node metadata records `metadata.ports` and `metadata.connection_policy`; the current runtime still uses `depends_on` as the executable dependency contract.
- Canvas navigation now supports mouse-wheel zoom and empty-canvas drag pan. Shift+drag starts rubber-band selection, Ctrl toggles multi-select, and node dragging stays clamped to the node type's semantic lane.
- Node runtime details now expose progress, effective Agent, the Agent's queued workflow tasks, available Agent-owned Skills, and repair suggestions. Node cards show an inline error badge for failed/blocked nodes; the detail panel still shows full definition/state/output/events.
- Agent-to-Skill mapping is many-to-many through ownership and promotion status. An Agent has an available Skill set (`owner_agent_id`, `active`/`candidate`), a node may explicitly call one Skill, and future routing can recommend Skills from triggers/tool allowlists. Do not hard-code one Skill per Agent.
- Self-repair is currently suggestion-first. The UI surfaces likely fixes such as run/repair upstream dependencies, fill missing `tool_name`/`skill_name`, check Skill ownership/status, claim/complete Agent tasks, or retry after parameter edits. Actual graph edits, disconnects, retries, and saves remain explicit user actions.

## Multi-Agent-Playground Takeaways

Reference: <https://github.com/Jasper-zh/Multi-Agent-Playground>. Fetch externally when a new comparison is required; no local clone is retained.

Useful patterns:

- Keep workflow types explicit. The repo separates `single_agent_chat`, `router_specialists`, `planner_executor`, `supervisor_dynamic`, and `peer_handoff`, which maps well to template governance in SpiritKin.
- Graph UI should make runtime visible, not just topology. Their graph combines static edges with trace-derived dynamic edges and highlights active/visited nodes.
- Edge endpoints should be boundary-aware. Group and node boundaries are calculated before drawing curved paths, which keeps linework from cutting through node bodies.
- Runtime traces should be first-class. Their trace event vocabulary covers run start/finish, node entry/exit, route selection, generated messages, and state updates.
- Supervisor workflows need shared workspace/artifact state. The dynamic supervisor passes current focus task, reports, workspace directory, generated files, and tool evidence through the loop.

Recommended additions for SpiritKin:

- Add edge labels and edge-state badges: static, runtime-selected, failed, blocked, waiting-review.
- Add a trace replay/timeline mode that animates node entry, route selection, tool calls, and state changes on the graph.
- Add group nodes/subgraph frames for peer handoff, supervisor loops, and reusable workflow templates.
- Persist viewport state: zoom, pan/scroll offsets, and selected node set per workflow.
- Extend the current execution-port compatibility rules into typed data/artifact/review ports: output type, input type, cardinality, cycle policy, and required review gate.

## Claude-Code-Game-Studios Takeaways

Reference: <https://github.com/Donchitos/Claude-Code-Game-Studios/>. Fetch externally when a new comparison is required; no local clone is retained.

Useful patterns:

- Agent structure is governance, not decoration. Directors, leads, and specialists have different decision rights and escalation paths.
- Phase gates prevent workflow drift. The project uses gates before phase transitions and has review modes for full, lean, and solo operation.
- Registries are critical for multi-agent safety. Their architecture registry models state ownership, interface contracts, API decisions, performance budgets, and forbidden patterns.
- Quality rubrics are machine-checkable contracts. Skills and agents are checked for verdict vocabulary, domain boundaries, blocked surfacing, parallel execution, and evidence format.
- Engine/model knowledge is version-aware. The project keeps version-pinned reference docs so agents do not rely only on stale LLM memory.

Recommended additions for SpiritKin:

- Add a workflow governance registry alongside workflow definitions:
  - state ownership: who can write run state, artifacts, task status, and knowledge entries.
  - interface contracts: event bus, direct call, tool call, shared artifact, or human gate.
  - forbidden patterns: hidden cross-agent writes, unreviewed rollback, unscoped shared memory, and uncited knowledge promotion.
  - quality gates: PASS / CONCERNS / FAIL or APPROVE / REJECT style verdicts per gate.
- Add role-aware permissions beyond metadata display. The current policy snapshot is useful, but the next layer should enforce write/rollback/run permissions at action time.
- Add skill/agent test rubrics for SpiritKin workflows: no silent fallback, no uncited KB promotion, no cross-agent state write without owner, no final answer without required gate evidence.
- Convert the process patterns into SpiritKin-owned Skills, not a direct copy of the external command set. Current seeded candidate Skills:
  `studio.project_stage_detect.workflow`, `studio.architecture_decision.workflow`, `studio.story_readiness.workflow`, `studio.dev_story.workflow`, `studio.gate_check.workflow`, and `studio.team_orchestration.workflow`.

## Wiki Recommendation

Yes, multi-agent information exchange should use wiki layers, but not as one loose shared notebook. Use three namespaced knowledge layers with provenance, owners, and version history.

1. Agent Wiki

   Purpose: what each agent is, what it owns, which tools/skills it can use, handoff rules, escalation rules, and recent operating notes.

   Suggested records: `agent_id`, role, capabilities, allowed tools, owned state, handoff contract, blocked conditions, review gates, current active workspace, last validated date.

2. LLM Wiki / Model Wiki

   Purpose: model cards and routing policy. This should capture provider, model, context window, cost tier, latency, strengths, known weak spots, safety constraints, and whether the information is verified against current docs.

   Suggested records: `model_id`, provider, best-use cases, disallowed-use cases, context/cost limits, tool support, structured-output support, fallback chain, last verification source.

3. Knowledge Base Wiki / Project Wiki

   Purpose: project facts and domain knowledge used by agents. This should be source-backed and retrievable by RAG, with citations and promotion workflow.

   Suggested records: `kb_id`, owner, scope, source path or URL, content hash, summary, tags, citations, confidence, promotion status, expiry/review date.

The exchange unit between agents should be an interaction packet, not raw memory:

- `packet_id`
- `from_agent_id`
- `to_agent_id` or `broadcast_scope`
- `workflow_id` / `run_id` / `node_id`
- `intent`: question, handoff, evidence, decision, review, rollback
- `summary`
- `state_delta`
- `artifact_refs`
- `knowledge_refs`
- `required_response`
- `expires_at`
- `audit_event_id`

This keeps multi-agent communication inspectable, replayable, and roll-backable.

Current implementation:

- `wiki_agent_registry`: Agent Wiki namespace for roles, ownership, capabilities, handoff contracts, and escalation paths.
- `wiki_model_registry`: LLM/Model Wiki namespace for model cards, routing policy, context/cost limits, and verification sources.
- `wiki_project_knowledge`: Project Wiki namespace for project facts, architecture decisions, citations, confidence, and review dates.
- Existing Agent knowledge bases remain intact; these system Wiki namespaces are auto-merged when missing.

## Next Implementation Order

1. Stabilize canvas interactions: persistent viewport, pan/minimap, edge labels, selection performance, line hit-testing.
2. Add runtime trace replay: show route decisions, tool calls, generated artifacts, and review gates on the graph.
3. Add governance registry: state owners, interface contracts, forbidden patterns, permission enforcement.
4. Upgrade knowledge management into Agent Wiki, Model Wiki, and Project Wiki namespaces using the existing knowledge-base screen as the foundation.
5. Add rubric-backed Agent and workflow tests before enabling autonomous multi-agent write access.
