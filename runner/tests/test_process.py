"""Tests for agent process management."""

import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runner.process import AgentProcess, ProcessState


class TestAgentProcess:
    """Tests for AgentProcess."""

    def test_initial_state(self):
        """Test initial state is PENDING."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
        )
        assert process.state == ProcessState.PENDING
        assert process.pid is None
        assert process.exit_code is None
        assert process.restart_count == 0

    def test_start_builds_correct_command(self, temp_dir):
        """Test that start builds the correct command."""
        process = AgentProcess(
            entity_id="alice",
            module="agents.random_agent",
            server_address="localhost:50051",
            args=["--eat-probability", "0.5"],
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            process.start()

            # Verify command structure
            call_args = mock_popen.call_args
            cmd = call_args[0][0]

            assert cmd[0] == "uv"
            assert cmd[1] == "run"
            assert cmd[2] == "python"
            assert cmd[3] == "-m"
            assert cmd[4] == "agents.random_agent"
            assert "--server" in cmd
            assert "localhost:50051" in cmd
            assert "--entity" in cmd
            assert "alice" in cmd
            assert "--eat-probability" in cmd
            assert "0.5" in cmd

    def test_start_sets_running_state(self, temp_dir):
        """Test that start sets state to RUNNING."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            process.start()

            assert process.state == ProcessState.RUNNING
            assert process.pid == 12345

    def test_start_raises_if_already_running(self, temp_dir):
        """Test that start raises if process is already running."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            process.start()

            with pytest.raises(RuntimeError, match="already running"):
                process.start()

    def test_poll_detects_exit(self, temp_dir):
        """Test that poll detects process exit."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None  # Still running
            mock_popen.return_value = mock_process

            process.start()
            assert process.poll() == ProcessState.RUNNING

            # Process exits with code 0
            mock_process.poll.return_value = 0
            assert process.poll() == ProcessState.STOPPED
            assert process.exit_code == 0

    def test_poll_detects_crash(self, temp_dir):
        """Test that poll detects process crash."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None
            mock_popen.return_value = mock_process

            process.start()

            # Process exits with non-zero code
            mock_process.poll.return_value = 1
            assert process.poll() == ProcessState.CRASHED
            assert process.exit_code == 1

    def test_stop_terminates_process(self, temp_dir):
        """Test that stop terminates the process."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None
            mock_process.returncode = 0
            mock_popen.return_value = mock_process

            process.start()
            process.stop(timeout=1.0)

            mock_process.terminate.assert_called_once()
            mock_process.wait.assert_called()
            assert process.state == ProcessState.STOPPED

    def test_stop_force_kills_on_timeout(self, temp_dir):
        """Test that stop force kills if graceful shutdown times out."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=temp_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.poll.return_value = None
            mock_process.returncode = -9
            mock_process.wait.side_effect = [
                subprocess.TimeoutExpired(cmd="test", timeout=1.0),
                None,
            ]
            mock_popen.return_value = mock_process

            process.start()
            process.stop(timeout=1.0)

            mock_process.terminate.assert_called_once()
            mock_process.kill.assert_called_once()

    def test_mark_restarting_increments_count(self):
        """Test that mark_restarting increments restart count."""
        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
        )

        assert process.restart_count == 0

        process.mark_restarting()
        assert process.restart_count == 1
        assert process.state == ProcessState.RESTARTING

        process.mark_restarting()
        assert process.restart_count == 2

    def test_creates_log_directory(self, temp_dir):
        """Test that log directory is created if it doesn't exist."""
        log_dir = temp_dir / "nested" / "logs"
        assert not log_dir.exists()

        process = AgentProcess(
            entity_id="test",
            module="agents.random_agent",
            server_address="localhost:50051",
            log_dir=log_dir,
        )

        with patch("subprocess.Popen") as mock_popen:
            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_popen.return_value = mock_process

            process.start()

            assert log_dir.exists()
