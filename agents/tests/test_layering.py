"""The package's import layering, enforced by parsing the source.

Every module in `agents.jev_agent` sits on a numbered layer and may only import
from a strictly lower one. That keeps the dependency graph acyclic without
anybody having to remember it, and it is why the shared vocabulary lives in
`briefs.py` and the planner's view of the tick loop in `bridge.py` rather than
in function-local imports scattered through the call sites.

Imports guarded by `if TYPE_CHECKING:` are exempt: they never run, so they
cannot make a cycle. `snapshot.py` uses one to name `JevAgent`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "agents" / "jev_agent"

# Layer 0 is the bottom. A module may import only from a layer below its own.
LAYERS: dict[str, int] = {
    # L0 - no intra-package dependencies at all.
    "client": 0,
    "geometry": 0,
    "items": 0,
    "jevclient": 0,
    "llm": 0,
    "outcomes": 0,
    "tracelog": 0,
    # L1 - the map and the arithmetic over it.
    "pathfinding": 1,
    "pricing": 1,
    "worldmodel": 1,
    # L2 - shared vocabulary and the standalone writers.
    "actions": 2,
    "briefs": 2,
    "enclosure": 2,
    "journal": 2,
    "recipes": 2,
    # L3 - walking, and what a tick may look like.
    "walk": 3,
    "conversation": 4,
    "jevstate": 4,
    "options": 4,
    "reflex": 4,
    "snapshot": 4,
    # L5 - running a brief.
    "stint": 5,
    # L6 - what the planner stands on.
    "bridge": 6,
    "build": 6,
    # L7 and up - the planner and the tick loop.
    "planner": 7,
    "agent": 8,
    "__main__": 9,
    # `__init__` is the package's public face and re-exports from everywhere.
    "__init__": 99,
}


def _module_files() -> list[Path]:
    return sorted(PACKAGE_DIR.glob("*.py"))


def _runtime_imports(source: str) -> set[str]:
    """Sibling modules `source` imports outside an `if TYPE_CHECKING:` block."""
    tree = ast.parse(source)
    type_checking_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in ast.walk(node):
                type_checking_nodes.add(id(child))
    siblings: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if id(node) in type_checking_nodes:
            continue
        if node.level == 1 and node.module:
            siblings.add(node.module.split(".")[0])
    return siblings


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def test_every_module_has_a_declared_layer() -> None:
    """A new module must be placed on a layer deliberately, not by accident."""
    found = {path.stem for path in _module_files()}
    assert found == set(LAYERS), (
        f"undeclared: {sorted(found - set(LAYERS))}; "
        f"declared but missing: {sorted(set(LAYERS) - found)}"
    )


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: p.stem)
def test_module_imports_only_from_lower_layers(path: Path) -> None:
    module = path.stem
    layer = LAYERS[module]
    for dependency in sorted(_runtime_imports(path.read_text())):
        if dependency not in LAYERS:
            continue
        assert LAYERS[dependency] < layer, (
            f"{module} (layer {layer}) imports {dependency} "
            f"(layer {LAYERS[dependency]}); imports must go strictly downward"
        )


def test_no_function_local_sibling_imports() -> None:
    """Sibling imports sit at module top level, where the layering is visible.

    The one exception is a `TYPE_CHECKING` block, which this check ignores
    because `_runtime_imports` does.
    """
    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and inner.level == 1:
                    offenders.append(f"{path.stem}.{node.name}: {inner.module}")
    assert offenders == []
