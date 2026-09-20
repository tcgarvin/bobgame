"""The package's import layering, enforced by parsing the source.

Every top-level module *or package* in `agents.jev_agent` sits on a numbered
layer and may only import from a strictly lower one. That keeps the dependency
graph acyclic without anybody having to remember it, and it is why the shared
vocabulary lives in `briefs.py` and the planner's view of the tick loop in
`bridge.py` rather than in function-local imports scattered through the call
sites.

A package counts as one unit at its declared layer: every file inside it, its
`__init__` included, may import siblings only from below that layer. Inside a
package the order is declared by `INTRA_ORDER` and checked the same way, so
`options.steps` cannot reach back up into `options.base`.

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
    "briefs": 2,
    "enclosure": 2,
    "journal": 2,
    "recipes": 2,
    # L3 - walking, and the one rule per action (which needs the seal check).
    "actions": 3,
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

# Inside a split package, the order its own submodules may import in. Bottom
# first; `__init__` is always the top and is added automatically.
INTRA_ORDER: dict[str, tuple[str, ...]] = {
    "conversation": ("protocol", "converser", "report", "session"),
    "agent": ("modes", "sleeping", "saving", "requests", "status", "core"),
    "planner": (
        "common",
        "prompt",
        "status",
        "validation",
        "describe",
        "toolset",
        "tools",
        "factory",
        "turn",
    ),
    "stint": ("endings", "records", "runner"),
    "worldmodel": ("types", "notices", "events", "payload", "model"),
    "options": (
        "common",
        "steps",
        "survival",
        "social",
        "interaction",
        "crafting",
        "base",
    ),
}


def _module_files() -> list[Path]:
    """Every source file in the package, top-level modules and package files."""
    return sorted(PACKAGE_DIR.rglob("*.py"))


def _unit(path: Path) -> str:
    """The top-level module or package `path` belongs to."""
    return path.relative_to(PACKAGE_DIR).parts[0].removesuffix(".py")


def _intra_position(path: Path, order: tuple[str, ...]) -> int:
    """How far up its own package `path` sits; `__init__` is above everything."""
    parts = path.relative_to(PACKAGE_DIR).parts[1:]
    name = parts[0].removesuffix(".py")
    if name == "__init__":
        return len(order)
    if name in order:
        return order.index(name)
    # A nested sub-package (planner/tools/) sits where its directory does.
    raise KeyError(name)


def _imports(source: str) -> list[tuple[int, str]]:
    """`(level, first name)` for every relative import outside `TYPE_CHECKING`."""
    tree = ast.parse(source)
    type_checking_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for child in ast.walk(node):
                type_checking_nodes.add(id(child))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if id(node) in type_checking_nodes:
            continue
        if node.module:
            found.append((node.level, node.module.split(".")[0]))
        else:
            # `from . import items` - the names are the modules.
            found.extend((node.level, alias.name) for alias in node.names)
    return found


def _is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def test_every_unit_has_a_declared_layer() -> None:
    """A new module must be placed on a layer deliberately, not by accident."""
    found = {_unit(path) for path in _module_files()}
    assert found == set(LAYERS), (
        f"undeclared: {sorted(found - set(LAYERS))}; "
        f"declared but missing: {sorted(set(LAYERS) - found)}"
    )


def test_every_split_package_declares_its_own_order() -> None:
    packages = {
        _unit(path)
        for path in _module_files()
        if len(path.relative_to(PACKAGE_DIR).parts) > 1
    }
    assert packages == set(INTRA_ORDER), (
        f"packages without an order: {sorted(packages - set(INTRA_ORDER))}; "
        f"orders without a package: {sorted(set(INTRA_ORDER) - packages)}"
    )


@pytest.mark.parametrize(
    "path",
    _module_files(),
    ids=lambda p: str(p.relative_to(PACKAGE_DIR)),
)
def test_module_imports_only_from_lower_layers(path: Path) -> None:
    """Sibling imports go strictly downward, whatever depth they are made at."""
    unit = _unit(path)
    layer = LAYERS[unit]
    depth = len(path.relative_to(PACKAGE_DIR).parts)
    for level, name in _imports(path.read_text()):
        # `level` counts the dots: inside `jev_agent/x.py` one dot is a
        # sibling; inside `jev_agent/pkg/x.py` it takes two.
        if level != depth:
            continue
        if name not in LAYERS:
            continue
        assert LAYERS[name] < layer, (
            f"{path.name} (unit {unit}, layer {layer}) imports {name} "
            f"(layer {LAYERS[name]}); imports must go strictly downward"
        )


@pytest.mark.parametrize(
    "path",
    [p for p in _module_files() if len(p.relative_to(PACKAGE_DIR).parts) > 1],
    ids=lambda p: str(p.relative_to(PACKAGE_DIR)),
)
def test_package_submodules_import_in_the_declared_order(path: Path) -> None:
    order = INTRA_ORDER[_unit(path)]
    position = _intra_position(path, order)
    for level, name in _imports(path.read_text()):
        if level != len(path.relative_to(PACKAGE_DIR).parts) - 1 or name not in order:
            continue
        assert order.index(name) < position, (
            f"{path.name} imports {name}, which is not below it in "
            f"{_unit(path)}'s declared order {order}"
        )


def test_no_function_local_sibling_imports() -> None:
    """Sibling imports sit at module top level, where the layering is visible.

    The one exception is a `TYPE_CHECKING` block, which this check ignores
    because `_imports` does.
    """
    offenders: list[str] = []
    for path in _module_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.ImportFrom) and inner.level:
                    offenders.append(f"{path.stem}.{node.name}: {inner.module}")
    assert offenders == []
