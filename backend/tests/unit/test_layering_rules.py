"""Import-direction rules for the app <-> orchestrator boundary (review item #5).

Declared rule: dependencies flow app -> orchestrator, one way. The reverse
direction (orchestrator importing backend.app) is legacy debt, frozen in the
allowlists below. The lists may only shrink: any new orchestrator -> app import
fails this test instead of silently deepening the cycle.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "orchestrator"
DEVICES_DIR = Path(__file__).resolve().parents[2] / "devices"

# Frozen debt: module-scope (import-time) orchestrator -> app imports.
# Emptied 2026-07-04: state_store/code_jury moved below the boundary,
# safety_control moved to backend.security, context_write_applier moved into
# backend.app. Any new entry is a regression.
KNOWN_TOP_LEVEL_APP_IMPORTS: set[str] = set()

# Frozen debt: function-scope (deferred) orchestrator -> app imports that mask
# the circularity at import time but still couple the layers.
# TODO(debt-#5): these defer-import app-domain snapshot builders; dissolving
# them needs an inversion (app registers providers with the orchestrator),
# not another module move. Shrink-only until then.
KNOWN_DEFERRED_APP_IMPORTS: set[str] = set()


def _references_backend_app(node: ast.Import | ast.ImportFrom) -> bool:
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module == "backend.app" or module.startswith("backend.app.")
    return any(alias.name == "backend.app" or alias.name.startswith("backend.app.") for alias in node.names)


def _collect_app_imports(path: Path) -> tuple[bool, bool]:
    """Return (has_top_level_app_import, has_deferred_app_import) for a module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top_level = False
    deferred = False

    def visit(node: ast.AST, in_function: bool) -> None:
        nonlocal top_level, deferred
        for child in ast.iter_child_nodes(node):
            child_in_function = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            if isinstance(child, (ast.Import, ast.ImportFrom)) and _references_backend_app(child):
                if in_function:
                    deferred = True
                else:
                    top_level = True
            visit(child, child_in_function)

    visit(tree, in_function=False)
    return top_level, deferred


def _scan(directory: Path) -> tuple[set[str], set[str]]:
    top_level_offenders: set[str] = set()
    deferred_offenders: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        top_level, deferred = _collect_app_imports(path)
        if top_level:
            top_level_offenders.add(path.name)
        if deferred:
            deferred_offenders.add(path.name)
    return top_level_offenders, deferred_offenders


class LayeringRulesTests(unittest.TestCase):
    def test_orchestrator_app_imports_do_not_grow(self):
        top_level, deferred = _scan(ORCHESTRATOR_DIR)
        new_top_level = top_level - KNOWN_TOP_LEVEL_APP_IMPORTS
        new_deferred = deferred - KNOWN_DEFERRED_APP_IMPORTS
        self.assertFalse(
            new_top_level,
            f"New orchestrator -> backend.app top-level imports in {sorted(new_top_level)}; "
            "the dependency rule is app -> orchestrator only. Move the shared code below the "
            "boundary instead of importing upward.",
        )
        self.assertFalse(
            new_deferred,
            f"New deferred orchestrator -> backend.app imports in {sorted(new_deferred)}; "
            "deferring the import hides the cycle but keeps the coupling. Move the shared "
            "code below the boundary instead.",
        )

    def test_orchestrator_app_import_allowlists_shrink_with_fixes(self):
        top_level, deferred = _scan(ORCHESTRATOR_DIR)
        stale_top_level = KNOWN_TOP_LEVEL_APP_IMPORTS - top_level
        stale_deferred = KNOWN_DEFERRED_APP_IMPORTS - deferred
        self.assertFalse(
            stale_top_level,
            f"{sorted(stale_top_level)} no longer import backend.app at top level — remove them "
            "from KNOWN_TOP_LEVEL_APP_IMPORTS so the debt cannot regress.",
        )
        self.assertFalse(
            stale_deferred,
            f"{sorted(stale_deferred)} no longer defer-import backend.app — remove them from "
            "KNOWN_DEFERRED_APP_IMPORTS so the debt cannot regress.",
        )

    def test_devices_never_import_app(self):
        top_level, deferred = _scan(DEVICES_DIR)
        self.assertFalse(top_level | deferred, f"backend.devices must stay app-free, found: {sorted(top_level | deferred)}")

    def test_orchestrator_has_no_backend_app_imports(self):
        top_level, deferred = _scan(ORCHESTRATOR_DIR)
        self.assertEqual((top_level, deferred), (set(), set()))


if __name__ == "__main__":
    unittest.main()
