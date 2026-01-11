# Runner Project Notes

Development notes and patterns for the agent runner.

## Overview

The runner discovers entities from the world server and spawns/manages agent processes with automatic restart and graceful shutdown.

## Architecture

```
runner/
├── src/runner/
│   ├── __main__.py     # CLI entry point
│   ├── config.py       # TOML config parsing (Pydantic)
│   ├── discovery.py    # gRPC entity discovery
│   ├── process.py      # AgentProcess wrapper
│   └── manager.py      # ProcessManager coordination
└── configs/
    └── foraging.toml   # Example config for foraging scenario
```

## Config Format

Uses TOML for consistency with `world/configs/*.toml`:

```toml
[runner]
server = "localhost:50051"
auto_discover = true
log_dir = "logs"
max_restart_attempts = 5
initial_backoff_ms = 1000
max_backoff_ms = 30000
backoff_multiplier = 2.0

[agents.default]
module = "agents.random_agent"
args = ["--eat-probability", "0.1"]

[agents.alice]
module = "agents.random_agent"
args = ["--eat-probability", "0.2"]
```

## Process Lifecycle

1. **Discovery**: Query EntityDiscoveryService for available entities
2. **Spawn**: Launch subprocess for each entity using `uv run python -m <module>`
3. **Monitor**: Poll process status every 500ms
4. **Restart**: On crash, schedule restart with exponential backoff
5. **Shutdown**: On SIGINT/SIGTERM, send SIGTERM to children, wait 5s, then SIGKILL

## Key Design Decisions

### Process vs Lease Monitoring

The runner monitors process status (is subprocess alive?) rather than lease status (is lease still valid?). This is simpler and sufficient because:
- If the process crashes, we need to restart anyway
- The agent handles its own lease renewal while running
- Lease expiry (30s) is longer than health check interval (0.5s)

### Exponential Backoff

Restart backoff prevents rapid crash loops:
- Start at 1 second
- Double each attempt up to 30 seconds max
- Stop after 5 attempts (configurable)

### Signal Handling

The runner catches SIGINT and SIGTERM to ensure clean shutdown:
- Sets shutdown flag to break main loop
- Propagates SIGTERM to all child processes
- Waits up to 5 seconds for graceful exit
- Force kills (SIGKILL) remaining processes

## Running

```bash
# With config file
cd runner && uv run python -m runner --config configs/foraging.toml

# With CLI overrides
cd runner && uv run python -m runner --server localhost:50051 --log-dir logs

# Or via dev.sh (uses runner automatically)
./dev.sh
```

## Testing

```bash
cd runner && uv run pytest tests/ -v
```

## Future Extensions

### JIT Agent Creation

The config structure supports future dynamic agent spawning:

```toml
[jit]
enabled = true
template = "default"
watch_interval_ms = 5000
```

This would allow spawning agents for entities discovered after startup.

### Lease-Based Health Monitoring

Could add optional lease monitoring to detect stuck agents (running but not responding):
- Query EntityDiscoveryService periodically
- If lease expired but process running, force restart
