"""The floor codes are a wire format shared with the viewer.

`bobgame_rules.terrain` is the Python source of truth and
`viewer/src/generated/rules.ts` is written from it by
`tools/generate_rules_ts.py`. Parsing the generated TypeScript here makes a
drift between the two fail CI instead of painting the island with the wrong
tiles; `tools/tests/test_generate_rules_ts.py` catches the generator not having
been run at all.
"""

import re
from pathlib import Path

import pytest

from world.terrain_types import FLOOR_TYPE_BY_CODE, FloorType

VIEWER_RULES_TS = (
    Path(__file__).resolve().parents[2] / "viewer" / "src" / "generated" / "rules.ts"
)

_ENUM_BLOCK = re.compile(r"export const FloorType = \{(.*?)\} as const;", re.DOTALL)
_ENTRY = re.compile(r"^\s*([A-Z_]+):\s*(\d+),\s*$", re.MULTILINE)


def _viewer_floor_codes() -> dict[str, int]:
    """The `FloorType` map the viewer compiles, as {NAME: code}."""
    source = VIEWER_RULES_TS.read_text(encoding="utf-8")
    block = _ENUM_BLOCK.search(source)
    if block is None:
        raise AssertionError(
            f"No `export const FloorType = {{...}} as const;` in {VIEWER_RULES_TS}"
        )
    return {name: int(code) for name, code in _ENTRY.findall(block.group(1))}


@pytest.mark.skipif(
    not VIEWER_RULES_TS.exists(), reason="viewer source is not checked out"
)
def test_viewer_floor_codes_match_the_enum() -> None:
    assert _viewer_floor_codes() == {
        floor_type.name: floor_type.code for floor_type in FloorType
    }


def test_codes_are_unique_and_round_trip() -> None:
    codes = [floor_type.code for floor_type in FloorType]
    assert len(codes) == len(set(codes))
    for floor_type in FloorType:
        assert FLOOR_TYPE_BY_CODE[floor_type.code] is floor_type
