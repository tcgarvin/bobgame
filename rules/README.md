# bobgame-rules

The game's rules in one dependency-free package: item and object kinds, the
recipe table, and the numbers behind food, health, fatigue, sleep, combat,
wolves, conversations, signs, the day clock and the terrain floor codes.

`world/` enforces them, `agents/` reasons about them, `tools/` reports on them
and `tools/generate_rules_ts.py` writes the subset the viewer needs into
`viewer/src/generated/rules.ts`. Nothing here imports anything outside the
standard library, and nothing here has behaviour beyond pure helpers over its
own tables.
