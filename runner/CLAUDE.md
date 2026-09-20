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
    └── hamlet.toml     # The only scenario (six jev_agent settlers)
```

## Config Format

Uses TOML for consistency with `world/configs/*.toml`:

```toml
[runner]
entity_types = ["player"]        # world-simulated NPCs (wolves) are excluded
server = "localhost:50051"
connection_timeout_ms = 60000
auto_discover = true
log_dir = "logs"
max_restart_attempts = 5
initial_backoff_ms = 2000
max_backoff_ms = 30000
backoff_multiplier = 2.0

[agents.default]
module = "agents.jev_agent"
args = ["--settlers", "6"]

[agents.alice]                   # optional per-entity override
module = "agents.jev_agent"
```

`Config.get_agent_config` falls back to the `default` section and raises
`KeyError` when there is neither an entry for the entity nor a default: there
is no built-in agent module. The agent derives its own log root from
`BOBGAME_RUN_DIR` (see docs/07_replay.md), so `log_dir` only matters for the
runner's own output.

`[runner]` also accepts `resume_from` (default `""`); see "Resuming" below.

## Resuming a saved run

Contract: [docs/14_new_moon_and_saves.md](../docs/14_new_moon_and_saves.md).

`--resume-from <dir>` names the save directory this run continues from — an
absolute path to the **parent** run's `saves/tick-<T>`. When it is set,
`ProcessManager._agent_args` appends `--resume-from <dir>` to *every* agent
command; each agent reads its own `agents/<entity_id>.json.gz` out of that one
directory.

The value is resolved in `runner/__main__.py` as `--resume-from`, else the
environment variable `$BOBGAME_RESUME_FROM` (which is what `./dev.sh --resume`
exports), else the config's `[runner] resume_from`, else nothing. It is logged
on `runner_starting` as `resume_from`.

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
- Start at `initial_backoff_ms` (default 1 second; `hamlet.toml` sets 2)
- Multiply by `backoff_multiplier` each attempt, capped at `max_backoff_ms`
- Stop after `max_restart_attempts` (5)

### Signal Handling

The runner catches SIGINT and SIGTERM to ensure clean shutdown:
- Sets shutdown flag to break main loop
- Propagates SIGTERM to all child processes
- Waits up to 5 seconds for graceful exit
- Force kills (SIGKILL) remaining processes

## Running

```bash
# Default config: the name `hamlet`, resolved to runner/configs/hamlet.toml
cd runner && uv run python -m runner

# By name or by path
cd runner && uv run python -m runner --config configs/hamlet.toml

# With CLI overrides
cd runner && uv run python -m runner --server localhost:50051 --log-dir logs

# Resuming a saved run (dev.sh --resume sets $BOBGAME_RESUME_FROM instead)
cd runner && uv run python -m runner --resume-from /abs/runs/<parent>/saves/tick-612

# Or via dev.sh (uses runner automatically)
./dev.sh
./dev.sh --resume <run_id>[@<tick>]
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
