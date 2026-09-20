"""The viewer's generated rules must match `bobgame_rules`, and its sprites
must cover every object type the world can place.

`viewer/src/generated/rules.ts` is checked in so the browser build needs no
Python. These tests make a rule change without a regeneration fail here rather
than in the browser.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generate_rules_ts  # noqa: E402

from bobgame_rules import items  # noqa: E402

VIEWER_SRC = generate_rules_ts.REPO_ROOT / "viewer" / "src"
GAME_SCENE_TS = VIEWER_SRC / "scenes" / "GameScene.ts"

# Object types GameScene draws without a row in OBJECT_SPRITE_MAP: a bush picks
# between BUSH_SPRITE_FULL and BUSH_SPRITE_EMPTY from its `berry_count`.
SPRITE_SPECIAL_CASES = frozenset({items.BUSH})

_SPRITE_MAP_BLOCK = re.compile(
    r"const OBJECT_SPRITE_MAP: Record<string, string> = \{(.*?)\n\};", re.DOTALL
)
_SPRITE_ENTRY = re.compile(r"^\s*([a-z_]+):\s*'[^']+',\s*$", re.MULTILINE)


def _sprite_mapped_types() -> set[str]:
    """The object types `GameScene.OBJECT_SPRITE_MAP` gives a sprite key."""
    block = _SPRITE_MAP_BLOCK.search(GAME_SCENE_TS.read_text(encoding="utf-8"))
    if block is None:
        raise AssertionError(f"No OBJECT_SPRITE_MAP in {GAME_SCENE_TS}")
    return set(_SPRITE_ENTRY.findall(block.group(1)))


def test_checked_in_rules_ts_is_up_to_date(tmp_path: Path) -> None:
    """Regenerating must not change the file the viewer compiles."""
    generated = tmp_path / "rules.ts"
    assert generate_rules_ts.main(["--out", str(generated)]) == 0
    assert generated.read_text(
        encoding="utf-8"
    ) == generate_rules_ts.OUTPUT_PATH.read_text(
        encoding="utf-8"
    ), "viewer/src/generated/rules.ts is stale; run tools/generate_rules_ts.py"


def test_check_mode_agrees() -> None:
    assert generate_rules_ts.main(["--check"]) == 0


@pytest.mark.skipif(
    not GAME_SCENE_TS.exists(), reason="viewer source is not checked out"
)
def test_every_object_kind_has_a_sprite() -> None:
    mapped = _sprite_mapped_types() | SPRITE_SPECIAL_CASES
    missing = sorted(items.OBJECT_TYPES - mapped)
    assert not missing, f"no sprite in GameScene.OBJECT_SPRITE_MAP for: {missing}"


@pytest.mark.skipif(
    not GAME_SCENE_TS.exists(), reason="viewer source is not checked out"
)
def test_no_sprite_maps_an_unknown_object_kind() -> None:
    unknown = sorted(_sprite_mapped_types() - items.OBJECT_TYPES)
    assert (
        not unknown
    ), f"OBJECT_SPRITE_MAP names object types the world never places: {unknown}"
