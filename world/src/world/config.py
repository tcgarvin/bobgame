"""World configuration loading from TOML files."""

import tomllib
from pathlib import Path

from pydantic import BaseModel

from .state import Entity, WorldObject
from .types import Position


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
