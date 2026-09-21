"""Tests for world config loading, the wolf tunables and the shipped configs."""

import pytest
from pydantic import ValidationError

from world.config import (
    WorldConfig,
    config_to_entities,
    find_config,
    list_configs,
    load_config,
)
from world.wolves import (
    DESPAWN_DISTANCE,
    MAX_WOLVES,
    SPAWN_INTERVAL_TICKS,
    SPAWN_MAX_DISTANCE,
    SPAWN_MIN_DISTANCE,
)


class TestWolfTunables:
    def test_defaults_are_the_tuned_constants(self) -> None:
        config = WorldConfig()
        assert config.max_wolves == MAX_WOLVES
        assert config.wolf_spawn_min_distance == SPAWN_MIN_DISTANCE
        assert config.wolf_spawn_max_distance == SPAWN_MAX_DISTANCE
        settings = config.wolf_settings()
        assert settings.max_wolves == MAX_WOLVES
        assert settings.spawn_min_distance == SPAWN_MIN_DISTANCE
        assert settings.spawn_max_distance == SPAWN_MAX_DISTANCE
        assert config.wolf_spawn_interval_ticks == SPAWN_INTERVAL_TICKS
        assert settings.spawn_interval_ticks == SPAWN_INTERVAL_TICKS

    def test_settings_carry_the_configured_values(self) -> None:
        settings = WorldConfig(
            max_wolves=1,
            wolf_spawn_min_distance=30,
            wolf_spawn_max_distance=45,
        ).wolf_settings()
        assert (settings.max_wolves, settings.spawn_min_distance) == (1, 30)
        assert settings.spawn_max_distance == 45
        slow = WorldConfig(wolf_spawn_interval_ticks=120).wolf_settings()
        assert slow.spawn_interval_ticks == 120

    def test_a_spawn_interval_below_one_is_refused(self) -> None:
        with pytest.raises(
            ValidationError, match="wolf_spawn_interval_ticks must be at least 1"
        ):
            WorldConfig(wolf_spawn_interval_ticks=0)

    def test_no_wolves_at_all_is_allowed(self) -> None:
        assert WorldConfig(max_wolves=0).wolf_settings().max_wolves == 0

    def test_a_negative_cap_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="max_wolves must be zero or more"):
            WorldConfig(max_wolves=-1)

    def test_a_zero_minimum_distance_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be at least 1"):
            WorldConfig(wolf_spawn_min_distance=0)

    def test_a_maximum_below_the_minimum_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be greater than"):
            WorldConfig(wolf_spawn_min_distance=30, wolf_spawn_max_distance=30)

    def test_a_maximum_at_or_past_the_despawn_distance_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="below the despawn distance"):
            WorldConfig(wolf_spawn_max_distance=DESPAWN_DISTANCE)


class TestShippedConfigs:
    def test_hamlet_has_six_settlers_and_one_distant_wolf(self) -> None:
        config = load_config(find_config("hamlet"))

        entities = config_to_entities(config)
        assert [e.entity_id for e in entities] == [
            "sloopa",
            "meduski",
            "pooka",
            "dov",
            "esme",
            "finn",
        ]
        assert all(e.entity_type == "player" for e in entities)
        assert config.world.wolves is True
        assert config.world.spawn_mode == "settlement"
        settings = config.world.wolf_settings()
        assert settings.max_wolves == 1
        assert settings.spawn_min_distance == 30
        assert settings.spawn_max_distance == 45
        assert settings.spawn_interval_ticks == 120

    def test_hamlet_is_the_only_shipped_config(self) -> None:
        assert list_configs() == ["hamlet"]

    def test_hamlet_generates_the_island_when_the_save_is_missing(self) -> None:
        config = load_config(find_config("hamlet"))

        assert config.world.generation_mode == "generate"
        assert config.world.map_save_path == "saves/island.npz"
        assert config.world.terrain_seed == 12345

    def test_an_unknown_config_name_is_refused(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            find_config("settlement")
