"""The tables have to hang together: every kind named, every recipe buildable."""

import pkgutil

import pytest

import bobgame_rules
from bobgame_rules import body, clock, entities, items, recipes, social, terrain


def test_package_imports_nothing_outside_the_standard_library() -> None:
    """A third-party import here would break world, agents and tools at once."""
    allowed = {"bobgame_rules", "dataclasses", "enum", "typing"}
    for module in pkgutil.iter_modules(bobgame_rules.__path__):
        source = (
            __import__(f"bobgame_rules.{module.name}", fromlist=["__file__"]).__file__
            or ""
        )
        for line in open(source, encoding="utf-8"):
            if line.startswith(("import ", "from ")):
                root = line.split()[1].lstrip(".").split(".")[0]
                assert root in allowed or line.startswith("from ."), line


def test_every_recipe_output_is_an_item_kind() -> None:
    for name in recipes.RECIPES:
        assert name in items.ITEM_KINDS, name


def test_every_recipe_input_is_an_item_kind() -> None:
    for name, recipe in recipes.RECIPES.items():
        for kind, count in recipe.inputs:
            assert kind in items.ITEM_KINDS, (name, kind)
            assert count > 0


def test_every_recipe_station_is_a_station() -> None:
    for name, recipe in recipes.RECIPES.items():
        assert recipe.station in ("", *items.STATION_KINDS), name
        assert recipe.work >= 1
        if not recipe.station:
            assert recipe.work == 1, name


def test_placeable_kinds_all_have_a_placed_object_type() -> None:
    assert set(items.PLACED_OBJECT_TYPE) == set(items.PLACEABLE_KINDS)


def test_building_kinds_are_item_kinds_and_object_types() -> None:
    assert items.BUILDING_KINDS <= items.ITEM_KINDS
    assert items.BUILDING_KINDS <= items.OBJECT_TYPES
    assert items.BLOCKING_OBJECT_TYPES <= items.BUILDING_KINDS
    assert items.WOLF_BLOCKING_OBJECT_TYPES >= items.BLOCKING_OBJECT_TYPES


def test_extraction_tables_cover_the_same_object_types() -> None:
    assert set(items.EXTRACT_YIELD) == items.EXTRACTABLE_TYPES
    assert set(items.DEFAULT_REMAINING) == items.EXTRACTABLE_TYPES
    assert set(items.EXTRACT_TOOLS) <= items.EXTRACTABLE_TYPES
    assert set(items.VEIN_REQUIRED_TOOLS) == items.VEIN_TYPES
    for yielded in items.EXTRACT_YIELD.values():
        assert yielded in items.ITEM_KINDS


@pytest.mark.parametrize(
    "wielded,object_type,expected",
    [
        ("", items.TREE, items.EXTRACT_WORK_BARE),
        (items.AXE, items.TREE, 3),
        (items.IRON_AXE, items.TREE, 5),
        # A tool only helps on what it is for.
        (items.AXE, "boulder", items.EXTRACT_WORK_BARE),
    ],
)
def test_extract_work(wielded: str, object_type: str, expected: int) -> None:
    assert items.extract_work(wielded, object_type) == expected


def test_can_extract_follows_the_vein_tiers() -> None:
    assert items.can_extract("", items.TREE)
    assert not items.can_extract("", items.COPPER_VEIN)
    assert items.can_extract(items.PICKAXE, items.COPPER_VEIN)
    assert not items.can_extract(items.PICKAXE, items.IRON_VEIN)
    assert items.can_extract(items.COPPER_PICKAXE, items.IRON_VEIN)


def test_source_object_types() -> None:
    assert items.source_object_types(items.FIBER) == [items.REEDS]
    assert items.source_object_types(items.BERRY) == []


def test_attack_damage() -> None:
    assert entities.attack_damage(entities.DEFAULT_ENTITY_TYPE, "") == 2
    assert entities.attack_damage(entities.WOLF_ENTITY_TYPE, "") == 3
    assert entities.attack_damage(entities.DEFAULT_ENTITY_TYPE, items.IRON_SWORD) == 7
    assert entities.WIELDABLE_KINDS <= items.ITEM_KINDS


def test_night_starts_two_thirds_through_the_day() -> None:
    assert clock.night_start_tick(clock.DEFAULT_DAY_LENGTH_TICKS) == 200
    assert clock.night_start_tick(10) == 6
    assert clock.night_start_fraction() == pytest.approx(2 / 3)


def test_sleep_recovery_prefers_a_bed() -> None:
    for night in (True, False):
        bed_points, bed_ticks = body.SLEEP_RECOVERY[(True, night)]
        ground_points, ground_ticks = body.SLEEP_RECOVERY[(False, night)]
        assert bed_points / bed_ticks > ground_points / ground_ticks


def test_body_thresholds_are_ordered() -> None:
    assert 0 < body.MIN_SLEEP_FATIGUE < body.TIRED_FATIGUE
    assert body.TIRED_FATIGUE < body.COLLAPSE_WAKE_FATIGUE < entities.PLAYER_MAX_FATIGUE
    assert 0 < body.HUNGRY_WAKE_FOOD < body.REGEN_FOOD_THRESHOLD
    assert body.RESPAWN_FOOD <= entities.PLAYER_MAX_FOOD


def test_hearing_radii_cover_every_audible_channel() -> None:
    assert set(social.HEARING_RADIUS_BY_CHANNEL) == social.AUDIBLE_CHANNELS
    assert social.SHOUT_RADIUS > social.SAY_RADIUS > social.VIEW_RADIUS


def test_floor_codes_are_unique_and_round_trip() -> None:
    codes = [floor_type.code for floor_type in terrain.FloorType]
    assert len(codes) == len(set(codes))
    for floor_type in terrain.FloorType:
        assert terrain.FLOOR_TYPE_BY_CODE[floor_type.code] is floor_type
