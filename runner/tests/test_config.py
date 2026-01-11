"""Tests for runner configuration."""

import pytest

from runner.config import (
    Config,
    RunnerConfig,
    AgentConfig,
    load_config,
)


class TestAgentConfig:
    """Tests for AgentConfig."""

    def test_defaults(self):
        """Test default values."""
        config = AgentConfig(module="agents.random_agent")
        assert config.module == "agents.random_agent"
        assert config.args == []
        assert config.env == {}

    def test_with_args(self):
        """Test with custom args."""
        config = AgentConfig(
            module="agents.random_agent",
            args=["--eat-probability", "0.5"],
            env={"DEBUG": "1"},
        )
        assert config.args == ["--eat-probability", "0.5"]
        assert config.env == {"DEBUG": "1"}


class TestRunnerConfig:
    """Tests for RunnerConfig."""

    def test_defaults(self):
        """Test default values."""
        config = RunnerConfig()
        assert config.server == "localhost:50051"
        assert config.connection_timeout_ms == 30000
        assert config.auto_discover is True
        assert config.log_dir == "logs"
        assert config.max_restart_attempts == 5
        assert config.initial_backoff_ms == 1000
        assert config.max_backoff_ms == 30000
        assert config.backoff_multiplier == 2.0


class TestConfig:
    """Tests for Config."""

    def test_defaults(self):
        """Test default values."""
        config = Config()
        assert config.runner.server == "localhost:50051"
        assert config.agents == {}

    def test_get_agent_config_specific(self):
        """Test getting entity-specific config."""
        config = Config(
            agents={
                "default": AgentConfig(module="agents.default"),
                "alice": AgentConfig(module="agents.alice"),
            }
        )
        assert config.get_agent_config("alice").module == "agents.alice"

    def test_get_agent_config_fallback_to_default(self):
        """Test fallback to default config."""
        config = Config(
            agents={
                "default": AgentConfig(module="agents.default"),
            }
        )
        assert config.get_agent_config("bob").module == "agents.default"

    def test_get_agent_config_raises_without_default(self):
        """Test error when no config and no default."""
        config = Config(
            agents={
                "alice": AgentConfig(module="agents.alice"),
            }
        )
        with pytest.raises(KeyError, match="No agent config for 'bob'"):
            config.get_agent_config("bob")


class TestLoadConfig:
    """Tests for load_config."""

    def test_load_config_basic(self, config_file):
        """Test loading a config file."""
        config = load_config(config_file)

        assert config.runner.server == "localhost:50051"
        assert config.runner.connection_timeout_ms == 5000
        assert config.runner.max_restart_attempts == 3

    def test_load_config_with_agents(self, config_file):
        """Test loading agent configs."""
        config = load_config(config_file)

        assert "default" in config.agents
        assert "alice" in config.agents

        assert config.agents["default"].module == "agents.random_agent"
        assert config.agents["default"].args == ["--eat-probability", "0.1"]

        assert config.agents["alice"].args == ["--eat-probability", "0.5"]

    def test_load_config_file_not_found(self, temp_dir):
        """Test error on missing file."""
        with pytest.raises(FileNotFoundError):
            load_config(temp_dir / "nonexistent.toml")
