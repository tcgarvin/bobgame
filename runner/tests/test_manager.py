"""Tests for process manager."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runner.config import Config, RunnerConfig, AgentConfig
from runner.discovery import DiscoveredEntity
from runner.manager import ProcessManager, RestartState
from runner.process import ProcessState


@pytest.fixture
def simple_config():
    """Simple config for testing."""
    return Config(
        runner=RunnerConfig(
            server="localhost:50051",
            connection_timeout_ms=1000,
            auto_discover=True,
            log_dir="test_logs",
            max_restart_attempts=3,
            initial_backoff_ms=100,
            max_backoff_ms=1000,
            backoff_multiplier=2.0,
        ),
        agents={
            "default": AgentConfig(module="agents.random_agent"),
        },
    )


@pytest.fixture
def discovered_entities():
    """Sample discovered entities."""
    return [
        DiscoveredEntity(
            entity_id="alice",
            entity_type="player",
            has_active_lease=False,
        ),
        DiscoveredEntity(
            entity_id="bob",
            entity_type="player",
            has_active_lease=False,
        ),
    ]


class TestProcessManager:
    """Tests for ProcessManager."""

    def test_spawn_agent_creates_process(self, simple_config, temp_dir):
        """Test that _spawn_agent creates an AgentProcess."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        entity = DiscoveredEntity(
            entity_id="alice",
            entity_type="player",
            has_active_lease=False,
        )

        with patch("runner.manager.AgentProcess") as mock_class:
            mock_process = MagicMock()
            mock_class.return_value = mock_process

            result = manager._spawn_agent(entity)

            assert result is True
            mock_class.assert_called_once()
            mock_process.start.assert_called_once()
            assert "alice" in manager._processes

    def test_spawn_agent_skips_leased_entity(self, simple_config, temp_dir):
        """Test that _spawn_agent skips already leased entities."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        entity = DiscoveredEntity(
            entity_id="alice",
            entity_type="player",
            has_active_lease=True,  # Already leased
        )

        result = manager._spawn_agent(entity)
        assert result is False
        assert "alice" not in manager._processes

    def test_spawn_agent_raises_without_config(self, temp_dir):
        """Test that _spawn_agent raises when no config available."""
        config = Config(
            runner=RunnerConfig(auto_discover=True),
            agents={},  # No default, no specific configs
        )

        manager = ProcessManager(
            config=config,
            working_dir=temp_dir,
        )

        entity = DiscoveredEntity(
            entity_id="alice",
            entity_type="player",
            has_active_lease=False,
        )

        with pytest.raises(RuntimeError, match="No agent config"):
            manager._spawn_agent(entity)

    def test_discover_and_spawn(self, simple_config, discovered_entities, temp_dir):
        """Test discover_and_spawn spawns agents for all entities."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        with (
            patch("runner.manager.wait_for_server") as mock_wait,
            patch("runner.manager.discover_entities") as mock_discover,
            patch("runner.manager.AgentProcess") as mock_class,
        ):
            mock_wait.return_value = True
            mock_discover.return_value = discovered_entities
            mock_process = MagicMock()
            mock_class.return_value = mock_process

            spawned = manager.discover_and_spawn()

            assert spawned == 2
            assert mock_class.call_count == 2
            assert "alice" in manager._processes
            assert "bob" in manager._processes

    def test_discover_and_spawn_raises_on_timeout(self, simple_config, temp_dir):
        """Test discover_and_spawn raises when server unavailable."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        with patch("runner.manager.wait_for_server") as mock_wait:
            mock_wait.return_value = False

            with pytest.raises(RuntimeError, match="Could not connect"):
                manager.discover_and_spawn()

    def test_handle_crash_schedules_restart(self, simple_config, temp_dir):
        """Test that _handle_crash schedules a restart."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        mock_process = MagicMock()
        manager._processes["alice"] = mock_process
        manager._restart_states["alice"] = RestartState(
            attempts=0,
            next_backoff_ms=100,
        )

        manager._handle_crash("alice", mock_process)

        mock_process.mark_restarting.assert_called_once()
        assert manager._restart_states["alice"].last_restart_time > 0

    def test_handle_crash_removes_after_max_attempts(self, simple_config, temp_dir):
        """Test that _handle_crash removes process after max attempts."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        mock_process = MagicMock()
        manager._processes["alice"] = mock_process
        manager._restart_states["alice"] = RestartState(
            attempts=3,  # Already at max (config says 3)
            next_backoff_ms=100,
        )

        manager._handle_crash("alice", mock_process)

        mock_process.mark_restarting.assert_not_called()
        assert "alice" not in manager._processes

    def test_shutdown_stops_all_processes(self, simple_config, temp_dir):
        """Test that shutdown stops all running processes."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        mock_alice = MagicMock()
        mock_alice.state = ProcessState.RUNNING
        mock_bob = MagicMock()
        mock_bob.state = ProcessState.RUNNING

        manager._processes["alice"] = mock_alice
        manager._processes["bob"] = mock_bob

        manager.shutdown(timeout=1.0)

        mock_alice.stop.assert_called_once_with(timeout=1.0)
        mock_bob.stop.assert_called_once_with(timeout=1.0)
        assert len(manager._processes) == 0

    def test_request_shutdown_sets_flags(self, simple_config, temp_dir):
        """Test that request_shutdown sets shutdown flags."""
        manager = ProcessManager(
            config=simple_config,
            working_dir=temp_dir,
        )

        manager._running = True
        manager.request_shutdown()

        assert manager._shutdown_requested is True
        assert manager._running is False


class TestRestartState:
    """Tests for RestartState."""

    def test_defaults(self):
        """Test default values."""
        state = RestartState()
        assert state.attempts == 0
        assert state.last_restart_time == 0.0
        assert state.next_backoff_ms == 1000

    def test_custom_values(self):
        """Test custom values."""
        state = RestartState(
            attempts=2,
            last_restart_time=1234.5,
            next_backoff_ms=500,
        )
        assert state.attempts == 2
        assert state.last_restart_time == 1234.5
        assert state.next_backoff_ms == 500
