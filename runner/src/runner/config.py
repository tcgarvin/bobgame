"""Runner configuration from TOML files."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for spawning an agent."""

    module: str  # Python module path, e.g., "agents.random_agent"
    args: list[str] = Field(default_factory=list)  # Additional CLI args
    env: dict[str, str] = Field(default_factory=dict)  # Env overrides


class RunnerConfig(BaseModel):
    """Runner settings."""

    server: str = "localhost:50051"
    connection_timeout_ms: int = 30000
    auto_discover: bool = True
    log_dir: str = "logs"
    max_restart_attempts: int = 5
    initial_backoff_ms: int = 1000
    max_backoff_ms: int = 30000
    backoff_multiplier: float = 2.0


class Config(BaseModel):
    """Complete runner configuration."""

    runner: RunnerConfig = Field(default_factory=RunnerConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)

    def get_agent_config(self, entity_id: str) -> AgentConfig:
        """Get agent config for entity, falling back to 'default'.

        Args:
            entity_id: The entity to get config for.

        Returns:
            AgentConfig for the entity.

        Raises:
            KeyError: If no config exists and no default defined.
        """
        if entity_id in self.agents:
            return self.agents[entity_id]
        if "default" in self.agents:
            return self.agents["default"]
        raise KeyError(f"No agent config for '{entity_id}' and no default defined")


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
    1. Exact path if name contains path separator or ends in .toml
    2. runner/configs/{name}.toml
    3. runner/configs/{name}

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

    # Try as-is
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
