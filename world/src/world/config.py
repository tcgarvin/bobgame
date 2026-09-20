"""World configuration loading from TOML files."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, field_validator, model_validator

from .state import DEFAULT_DAY_LENGTH_TICKS, Entity, WorldObject
from .types import Position
from .wolves import (
    DESPAWN_DISTANCE,
    MAX_WOLVES,
    SPAWN_INTERVAL_TICKS,
    SPAWN_MAX_DISTANCE,
    SPAWN_MIN_DISTANCE,
    WolfSettings,
)

VALID_SPAWN_MODES = frozenset({"positions", "settlement"})


class EntityConfig(BaseModel):
    """Entity configuration from TOML."""

    id: str
    type: str = "player"
    x: int
    y: int


class ObjectConfig(BaseModel):
    """Object configuration from TOML."""

    id: str
    type: str
    x: int
    y: int
    has_berry: bool = True  # For bush objects: whether it starts with a berry


class WorldConfig(BaseModel):
    """World configuration from TOML."""

    width: int = 10
    height: int = 10
    tick_duration_ms: int = 1000

    # Terrain generation mode: "empty", "generate", or "load"
    generation_mode: str = "empty"
    # Random seed for terrain generation (used when generation_mode="generate")
    terrain_seed: int | None = None
    # Path to saved map file for saving/loading (relative paths resolved from project root)
    # When generation_mode="generate": load if exists, else generate and save
    # When generation_mode="load": required, load from this path
    map_save_path: str | None = None

    # How entities are placed: "positions" uses each entity's configured x/y,
    # "settlement" spawns everyone near the computed settlement centre.
    spawn_mode: str = "positions"
    # Intent deadline within a tick. None means half the tick duration.
    intent_deadline_ms: int | None = None
    # Whether the world simulates wolves.
    wolves: bool = False
    # How many wolves live in the world at once.
    max_wolves: int = MAX_WOLVES
    # How far from a settler a new wolf appears, in tiles. Both bounds must
    # stay under the despawn distance, or a wolf would be culled on arrival.
    wolf_spawn_min_distance: int = SPAWN_MIN_DISTANCE
    wolf_spawn_max_distance: int = SPAWN_MAX_DISTANCE
    # Ticks between wolf spawn attempts. A longer interval means a killed
    # wolf stays gone for longer.
    wolf_spawn_interval_ticks: int = SPAWN_INTERVAL_TICKS
    # Ticks in one day/night cycle (docs/10_metal_and_sleep.md).
    day_length_ticks: int = DEFAULT_DAY_LENGTH_TICKS
    # The new moon (docs/14_new_moon_and_saves.md). 0 means the world never
    # has one: nobody is forced to sleep and no save is ever taken.
    new_moon_every_days: int = 0
    # Whether a new-moon night also writes a save.
    save_on_new_moon: bool = True
    # How long the world waits for the settlers' snapshot files, in seconds.
    save_wait_seconds: int = 180

    @field_validator("new_moon_every_days")
    @classmethod
    def _check_new_moon_every_days(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"new_moon_every_days must be zero or more, got {value}")
        return value

    @field_validator("save_wait_seconds")
    @classmethod
    def _check_save_wait_seconds(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"save_wait_seconds must be at least 1, got {value}")
        return value

    @field_validator("max_wolves")
    @classmethod
    def _check_max_wolves(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"max_wolves must be zero or more, got {value}")
        return value

    @field_validator("wolf_spawn_interval_ticks")
    @classmethod
    def _check_wolf_spawn_interval(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"wolf_spawn_interval_ticks must be at least 1, got {value}"
            )
        return value

    @model_validator(mode="after")
    def _check_wolf_distances(self) -> "WorldConfig":
        minimum = self.wolf_spawn_min_distance
        maximum = self.wolf_spawn_max_distance
        if minimum < 1:
            raise ValueError(
                f"wolf_spawn_min_distance must be at least 1, got {minimum}"
            )
        if maximum <= minimum:
            raise ValueError(
                "wolf_spawn_max_distance must be greater than "
                f"wolf_spawn_min_distance, got {maximum} <= {minimum}"
            )
        if maximum >= DESPAWN_DISTANCE:
            raise ValueError(
                "wolf_spawn_max_distance must be below the despawn distance "
                f"({DESPAWN_DISTANCE}), got {maximum}"
            )
        return self

    def wolf_settings(self) -> WolfSettings:
        """The wolf tunables as the simulator takes them."""
        return WolfSettings(
            max_wolves=self.max_wolves,
            spawn_min_distance=self.wolf_spawn_min_distance,
            spawn_max_distance=self.wolf_spawn_max_distance,
            spawn_interval_ticks=self.wolf_spawn_interval_ticks,
        )

    @field_validator("day_length_ticks")
    @classmethod
    def _check_day_length(cls, value: int) -> int:
        if value < 1:
            raise ValueError(f"day_length_ticks must be positive, got {value}")
        return value

    @field_validator("spawn_mode")
    @classmethod
    def _check_spawn_mode(cls, value: str) -> str:
        if value not in VALID_SPAWN_MODES:
            raise ValueError(
                f"spawn_mode must be one of {sorted(VALID_SPAWN_MODES)}, got {value!r}"
            )
        return value

    def resolved_intent_deadline_ms(self) -> int:
        """Intent deadline in ms, defaulting to half the tick duration."""
        if self.intent_deadline_ms is None:
            return self.tick_duration_ms // 2
        return self.intent_deadline_ms


class Config(BaseModel):
    """Complete configuration for a world server."""

    world: WorldConfig = WorldConfig()
    entities: list[EntityConfig] = []
    objects: list[ObjectConfig] = []


def load_config(config_path: Path) -> Config:
    """Load configuration from a TOML file.

    Args:
        config_path: Path to the TOML config file.

    Returns:
        Parsed Config object.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        tomllib.TOMLDecodeError: If TOML is malformed.
    """
    with open(config_path, "rb") as f:
        data = tomllib.load(f)
    return Config.model_validate(data)


def find_config(name: str) -> Path:
    """Find a config file by name.

    Searches in the following order:
    1. Exact path if name contains path separator
    2. world/configs/{name}.toml
    3. world/configs/{name}

    Args:
        name: Config name or path.

    Returns:
        Path to the config file.

    Raises:
        FileNotFoundError: If config file is not found.
    """
    # If it looks like a path, use it directly
    if "/" in name or name.endswith(".toml"):
        path = Path(name)
        if path.exists():
            return path
        raise FileNotFoundError(f"Config file not found: {name}")

    # Search in configs directory
    configs_dir = Path(__file__).parent.parent.parent / "configs"

    # Try with .toml extension
    config_path = configs_dir / f"{name}.toml"
    if config_path.exists():
        return config_path

    # Try as-is (for backwards compatibility)
    config_path = configs_dir / name
    if config_path.exists():
        return config_path

    raise FileNotFoundError(
        f"Config '{name}' not found in {configs_dir}. "
        f"Available configs: {list_configs()}"
    )


def list_configs() -> list[str]:
    """List available config names."""
    configs_dir = Path(__file__).parent.parent.parent / "configs"
    if not configs_dir.exists():
        return []
    return [p.stem for p in configs_dir.glob("*.toml")]


def config_to_entities(config: Config) -> list[Entity]:
    """Convert config entities to Entity objects."""
    return [
        Entity(
            entity_id=e.id,
            position=Position(x=e.x, y=e.y),
            entity_type=e.type,
        )
        for e in config.entities
    ]


def config_to_objects(config: Config) -> list[WorldObject]:
    """Convert config objects to WorldObject objects."""
    objects = []
    for obj in config.objects:
        if obj.type == "bush":
            # Binary berry state: "1" if has_berry, "0" otherwise
            berry_count = "1" if obj.has_berry else "0"
            objects.append(
                WorldObject(
                    object_id=obj.id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type="bush",
                    state=(("berry_count", berry_count),),
                )
            )
        else:
            objects.append(
                WorldObject(
                    object_id=obj.id,
                    position=Position(x=obj.x, y=obj.y),
                    object_type=obj.type,
                )
            )
    return objects
