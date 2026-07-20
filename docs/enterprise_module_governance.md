# SpiritKinAI Enterprise Module Governance

Last updated: 2026-06-06

## Purpose

This document defines how SpiritKinAI keeps every major module visible, owned, reviewed, and verifiable as the system grows from a personal assistant into an enterprise-managed AI runtime.

The executable source of truth is `backend/app/module_governance.py`. The ecosystem review surface imports that inventory through `backend/app/ecosystem_review.py` and exposes it as the `module_governance` dimension.

## Inventory Contract

Each module record must include:

- `module_id`: stable identifier used by dashboards and proposals.
- `path`: file or directory boundary owned by the module.
- `layer`: architecture layer, such as runtime, orchestration, knowledge, security, frontend, deployment, or data.
- `owner_role`: accountable role for review and release readiness.
- `criticality`: release impact: `critical`, `high`, or `medium`.
- `runtime_surface`: where the module affects runtime behavior.
- `expected_controls`: tests, docs, runbook, audit, policy, smoke test, or data controls expected for the module.
- `verification_commands`: commands a release operator can run without reverse engineering the module.
- `maturity_score`, `maturity_level`, `risk_level`, `gaps`, and `improvement_actions`.

The inventory currently tracks the backend entrypoint, application gateway, orchestrator, agents, tools, executors, devices, action semantics, knowledge, memory, perception, expression, services, security, eval, training, skills, events, mobile, remote, search, tests, frontend, desktop, scripts, docs, deploy, config, and data domains.

## Operating Model

Daily:

- Review service health, runtime logs, learning records, and pending ecosystem proposals.
- Keep high-risk module governance proposals in the human queue; they must not auto-apply.

Weekly:

- Review top module risks from `module_governance.portfolio.top_risks`.
- Close stale documentation gaps for critical and high modules.
- Add or update targeted tests when a module changes behavior.

Release:

- Block release when a critical module has high risk, failing tests, or unmanaged external write paths.
- Run each changed module's `verification_commands`.
- Confirm all high-risk `manual.module_governance` proposals have an owner decision.

## Verification

Use these commands after changing governance, ecosystem review, or command gateway behavior:

```powershell
python -m py_compile backend\app\module_governance.py backend\app\ecosystem_review.py
python -m unittest backend.tests.unit.test_ecosystem_review -v
python -m unittest backend.tests.unit.test_command_gateway -v
```

For broader release confidence, run:

```powershell
python -m unittest discover backend.tests.unit -v
python scripts/validate_desktop_delivery.py
dotnet build desktop\SpiritKinDesktop\SpiritKinDesktop.csproj --no-restore -p:UseAppHost=false
```

## Review Rules

- Critical and high modules require an owner role, a verification command, and either tests or a documented manual validation path.
- Runtime, execution, security, remote, mobile, and external-write surfaces require explicit approval gates before risky actions.
- Learning, Skill, model, and knowledge changes should enter the review queue before promotion when they can alter future behavior.
- Module governance proposals are intentionally manual actions. They should describe the gap and verification path, not mutate code automatically.

## Reading The Snapshot

Call `build_module_governance_snapshot(project_root=...)` for a standalone governance report. In the desktop ecosystem review response, read:

- `score.dimensions[]` where `dimension_id == "module_governance"`.
- `systems.module_governance.portfolio` for overall score, risk counts, maturity counts, and top risks.
- `systems.module_governance.modules` for per-module records.
- `systems.module_governance.improvement_backlog` for owner-assigned remediation actions.
- `proposals[]` with `manual.module_governance` actions for human approval and tracking.
