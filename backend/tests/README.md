# Backend Test Assets

This directory owns automated and manual validation assets for backend behavior. The QA Owner maintains this boundary, keeps tests isolated from production state, and makes sure release checks can run from documented commands.

## Scope

- Unit tests under `unit/` for management APIs, orchestration, tools, devices, memory, training, and runtime policy.
- Manual test notes under `manual/` for desktop and device flows that need operator validation.
- Shared fixtures should stay local to tests unless they are also production runtime contracts.

## Ownership

Large test areas should be grouped by runtime domain before adding unrelated cases to an existing file:

- Desktop and command gateway: `unit/test_command_gateway.py`, `unit/test_module_management.py`, `unit/test_ecosystem_review.py`.
- Knowledge and Search/RAG: `unit/test_knowledge_base_management.py`, `unit/test_incremental_indexer.py`, Search/RAG focused tests.
- Skills, learning, and evolution: Skill layer, promotion, replay, self-improvement, training workbench tests.
- Device and remote execution: Android, iOS, local PC, remote worker, and screen/audio bridge tests.

## Verification

Run the full backend test suite before release:

```powershell
python -m unittest discover backend.tests.unit -v
```

For focused desktop governance work, run:

```powershell
python -m unittest backend.tests.unit.test_command_gateway backend.tests.unit.test_module_management backend.tests.unit.test_ecosystem_review -v
```
