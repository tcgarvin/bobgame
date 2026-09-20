"""Write the viewer's copy of the game's rules from `bobgame_rules`.

The viewer used to hand-copy floor codes, blocking sets, dismantle and extract
work, the tired threshold and the night boundary. It now imports them from
`viewer/src/generated/rules.ts`, which this script writes and which is checked
in. `tools/tests/test_generate_rules_ts.py` regenerates it into a temporary
file and fails when the checked-in copy has drifted, so changing a rule without
running this fails the tests.

Usage:
    cd tools && uv run python generate_rules_ts.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bobgame_rules import body, clock, items, social, terrain

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "viewer" / "src" / "generated" / "rules.ts"

HEADER = """\
/**
 * GENERATED FILE - DO NOT EDIT.
 *
 * Written by `tools/generate_rules_ts.py` from `bobgame_rules` (repo root
 * `rules/`), the one definition of the game's rules. Change a rule there and
 * regenerate:
 *
 *     cd tools && uv run python generate_rules_ts.py
 */
"""


def _string_set(name: str, doc: str, values: list[str]) -> str:
    """A `ReadonlySet<string>` literal, sorted so the output is stable."""
    body_lines = "\n".join(f"  '{value}'," for value in sorted(values))
    return f"/** {doc} */\nexport const {name}: ReadonlySet<string> = new Set([\n{body_lines}\n]);\n"


def _number(name: str, doc: str, value: int | float) -> str:
    """A `const` number, written as an integer when it is one."""
    literal = str(value) if isinstance(value, int) else repr(float(value))
    return f"/** {doc} */\nexport const {name} = {literal};\n"


def render() -> str:
    """The whole of `rules.ts` as text."""
    floor_entries = "\n".join(
        f"  {floor_type.name}: {floor_type.code},"
        for floor_type in sorted(terrain.FloorType, key=lambda f: f.code)
    )
    sections = [
        HEADER,
        "/** Terrain floor codes, as stored in a saved map and sent to the viewer. */\n"
        f"export const FloorType = {{\n{floor_entries}\n}} as const;\n",
        "export type FloorType = (typeof FloorType)[keyof typeof FloorType];\n",
        _string_set(
            "OBJECT_TYPES",
            "Every object type the world can place; each needs a sprite.",
            sorted(items.OBJECT_TYPES),
        ),
        _string_set(
            "BUILDING_TYPES",
            "Placed buildings: they carry an owner and can be dismantled.",
            sorted(items.BUILDING_KINDS),
        ),
        _string_set(
            "GROUND_LAYER_TYPES",
            "Ground-layer buildings: they lie under structures and never block.",
            sorted(items.GROUND_LAYER_KINDS),
        ),
        _string_set(
            "BLOCKING_TYPES",
            "Object types that stop someone walking through (a door stops only wolves).",
            sorted(items.WOLF_BLOCKING_OBJECT_TYPES),
        ),
        _string_set(
            "STATION_TYPES",
            "Crafting stations, which hold per-settler craft progress.",
            sorted(items.STATION_KINDS),
        ),
        _string_set(
            "EXTRACTABLE_TYPES",
            "Natural objects worked with `extract` rather than opened.",
            sorted(items.EXTRACTABLE_TYPES),
        ),
        _number(
            "DISMANTLE_WORK",
            "Work units (extract actions) needed to dismantle a building.",
            items.DISMANTLE_WORK,
        ),
        _number(
            "EXTRACT_THRESHOLD",
            "Work units for one unit of material from a natural object.",
            items.EXTRACT_THRESHOLD,
        ),
        _number(
            "SIGN_TEXT_MAX",
            "Characters a sign holds on its single line.",
            social.SIGN_TEXT_MAX,
        ),
        _number(
            "TIRED_FATIGUE",
            "Fatigue at which a settler counts as tired.",
            body.TIRED_FATIGUE,
        ),
        _number(
            "NIGHT_START_FRACTION",
            "Daytime is the first two thirds of a day; night is the rest.",
            clock.night_start_fraction(),
        ),
    ]
    return "\n".join(sections)


def main(argv: list[str] | None = None) -> int:
    """Write `rules.ts`, or with `--check` report whether it is up to date."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the checked-in file differs, writing nothing",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUTPUT_PATH,
        help=f"where to write (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args(argv)

    rendered = render()
    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist", file=sys.stderr)
            return 1
        if args.out.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.out} is out of date; run generate_rules_ts.py", file=sys.stderr
            )
            return 1
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
