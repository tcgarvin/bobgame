#!/bin/bash
#
# Development script for bobgame
# Starts all components and handles cleanup on exit
#
# Usage:
#   ./dev.sh [config]
#
# Arguments:
#   config - Optional config name (default: island)
#            Available: default, foraging, island (see world/configs/)
#
# The island config generates a 4000x4000 procedural world on first run
# and saves it to saves/island.npz. Subsequent runs load the existing map.
#
# Starts:
#   - World server using the specified config
#   - Replay server (serves recorded runs to the viewer)
#   - Runner (manages agent processes with auto-restart)
#   - Viewer dev server
#
# Every run gets its own directory, runs/<run_id>, where <run_id> is
# YYYYMMDD-HHMMSS-<config>. All recordings and logs go there:
#   runs/<run_id>/meta.json
#   runs/<run_id>/world.log, runner.log, viewer.log, replay.log
#   runs/<run_id>/world/ticks.jsonl.gz, objects.jsonl.gz
#   runs/<run_id>/agents/agent-<id>.log, agent-<id>/*.jsonl.gz
# runs/latest is a symlink to the most recent run.
#
# See docs/07_replay.md for the recording format and replay protocol.
#
# Press Ctrl+C to stop all components
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDS=()

# Default config
CONFIG="${1:-island}"

# Parse arguments
for arg in "$@"; do
    case $arg in
        --help|-h)
            echo "Usage: $0 [config]"
            echo ""
            echo "Arguments:"
            echo "  config  Config name from world/configs/ (default: island)"
            echo ""
            echo "Starts all components for development:"
            echo "  - World server (gRPC :50051, WebSocket :8765)"
            echo "  - Replay server (WebSocket :8766, serves runs/)"
            echo "  - Agents via the runner"
            echo "  - Viewer (http://localhost:5173)"
            echo ""
            echo "Each run is recorded to runs/<YYYYMMDD-HHMMSS-config>/ and"
            echo "runs/latest points at it. Process logs live there too."
            echo "Replay an earlier run with ./replay.sh [run_id] and"
            echo "summarise one with python tools/analyze_run.py [run_dir]."
            echo ""
            echo "The island config generates a 4000x4000 procedural world on first"
            echo "run and saves it to saves/island.npz. Subsequent runs load the"
            echo "existing map for faster startup."
            echo ""
            echo "Available configs:"
            for cfg in "$SCRIPT_DIR/world/configs"/*.toml; do
                echo "  - $(basename "${cfg%.toml}")"
            done
            exit 0
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[dev]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[dev]${NC} $1"
}

log_error() {
    echo -e "${RED}[dev]${NC} $1"
}

# Cleanup function - kill all spawned processes and their children
cleanup() {
    echo ""
    log_info "Shutting down all components..."

    # First, send TERM to tracked PIDs
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping process $pid"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait a moment for graceful shutdown
    sleep 1

    # Kill all remaining child processes of this script
    # This catches any processes spawned via pipes (like tail | while)
    pkill -TERM -P $$ 2>/dev/null || true
    sleep 0.5
    pkill -9 -P $$ 2>/dev/null || true

    log_info "All components stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM EXIT

# Load API keys for agents (TYPESAFE_API_KEY, OPENROUTER_API_KEY)
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$SCRIPT_DIR/.env"
    set +a
fi

# Create the run directory and publish it to every child process.
# The world server and the agents read BOBGAME_RUN_DIR (see docs/07_replay.md).
RUN_ID="$(date +%Y%m%d-%H%M%S)-$CONFIG"
RUN_DIR="$SCRIPT_DIR/runs/$RUN_ID"
mkdir -p "$RUN_DIR/agents"
ln -sfn "$RUN_ID" "$SCRIPT_DIR/runs/latest"
export BOBGAME_RUN_ID="$RUN_ID"
export BOBGAME_RUN_DIR="$RUN_DIR"

log_info "Run id: $RUN_ID"
log_info "Run dir: $RUN_DIR"

# Function to tail logs with color prefix
tail_log() {
    local name=$1
    local color=$2
    local file=$3

    tail -f "$file" 2>/dev/null | while IFS= read -r line; do
        echo -e "${color}[$name]${NC} $line"
    done &
    PIDS+=($!)
}

# Wait for a port to be available
wait_for_port() {
    local port=$1
    local name=$2
    local max_attempts=${3:-30}
    local attempt=0

    log_info "Waiting for $name to be ready on port $port..."
    while ! nc -z localhost "$port" 2>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -ge $max_attempts ]; then
            log_error "$name did not start within ${max_attempts}s"
            return 1
        fi
        sleep 1
    done
    log_info "$name is ready on port $port"
}

# Start World Server
log_info "Starting World Server with config '$CONFIG'..."

# Check if this config might need terrain generation
NEEDS_GENERATION=false
if [ "$CONFIG" = "island" ] && [ ! -f "$SCRIPT_DIR/saves/island.npz" ]; then
    NEEDS_GENERATION=true
    log_warn "First run with island config - generating 4000x4000 terrain..."
    log_warn "This may take 2-5 minutes. Progress will be shown below."
    echo ""
fi

cd "$SCRIPT_DIR/world"
uv run python -m world.server \
    --config "$CONFIG" \
    > "$RUN_DIR/world.log" 2>&1 &
WORLD_PID=$!
PIDS+=($WORLD_PID)
log_info "World Server started (PID: $WORLD_PID)"

# If generating terrain, stream the log while waiting
if [ "$NEEDS_GENERATION" = true ]; then
    # Start tailing log in background
    tail -f "$RUN_DIR/world.log" 2>/dev/null | while IFS= read -r line; do
        echo -e "${CYAN}[world]${NC} $line"
    done &
    TAIL_PID=$!

    # Wait for world server with longer timeout (5 minutes)
    wait_for_port 50051 "World gRPC" 300 || { kill $TAIL_PID 2>/dev/null; exit 1; }
    wait_for_port 8765 "World WebSocket" 30 || { kill $TAIL_PID 2>/dev/null; exit 1; }

    # Stop the log tail
    kill $TAIL_PID 2>/dev/null
    echo ""
    log_info "Terrain generation complete!"
else
    # Normal startup - 30 second timeout
    wait_for_port 50051 "World gRPC" 30 || exit 1
    wait_for_port 8765 "World WebSocket" 30 || exit 1
fi

# Start Replay Server (serves recorded runs, including the one in progress)
log_info "Starting Replay Server..."
cd "$SCRIPT_DIR/world"
uv run python -m world.replay \
    --runs-dir "$SCRIPT_DIR/runs" \
    --port 8766 \
    > "$RUN_DIR/replay.log" 2>&1 &
REPLAY_PID=$!
PIDS+=($REPLAY_PID)
if wait_for_port 8766 "Replay WebSocket" 20; then
    log_info "Replay Server started (PID: $REPLAY_PID)"
    REPLAY_OK=true
else
    log_warn "Replay server did not start; see $RUN_DIR/replay.log (continuing)"
    REPLAY_OK=false
fi

# Start Runner (manages all agents)
# Use a runner config matching the world config name when one exists.
RUNNER_CONFIG="$SCRIPT_DIR/runner/configs/$CONFIG.toml"
if [ ! -f "$RUNNER_CONFIG" ]; then
    RUNNER_CONFIG="$SCRIPT_DIR/runner/configs/foraging.toml"
fi
log_info "Starting Agent Runner with $(basename "$RUNNER_CONFIG")..."
cd "$SCRIPT_DIR/runner"
uv run python -m runner \
    --config "$RUNNER_CONFIG" \
    --log-dir "$RUN_DIR/agents" \
    > "$RUN_DIR/runner.log" 2>&1 &
RUNNER_PID=$!
PIDS+=($RUNNER_PID)
log_info "Agent Runner started (PID: $RUNNER_PID)"

# Give runner a moment to spawn agents
sleep 2

# Start Viewer Dev Server
log_info "Starting Viewer..."
cd "$SCRIPT_DIR/viewer"
npm run dev > "$RUN_DIR/viewer.log" 2>&1 &
VIEWER_PID=$!
PIDS+=($VIEWER_PID)
log_info "Viewer started (PID: $VIEWER_PID)"

# Wait for viewer to be ready
wait_for_port 5173 "Viewer" || exit 1

# Now tail all logs with colors
echo ""
log_info "All components started successfully!"
echo ""
echo -e "  ${CYAN}World Server${NC}:  http://localhost:50051 (gRPC), ws://localhost:8765 (WebSocket)"
if [ "$REPLAY_OK" = true ]; then
    echo -e "  ${CYAN}Replay Server${NC}: ws://localhost:8766"
else
    echo -e "  ${RED}Replay Server${NC}: not running (see replay.log)"
fi
echo -e "  ${YELLOW}Runner${NC}:        Managing agents (with auto-restart)"
echo -e "  ${MAGENTA}Viewer${NC}:        http://localhost:5173"
echo ""
echo -e "  ${BLUE}Run id${NC}:        $RUN_ID"
echo -e "  ${BLUE}Run dir${NC}:       $RUN_DIR"
echo -e "  ${BLUE}Live${NC}:          http://localhost:5173"
echo -e "  ${BLUE}Replay${NC}:        http://localhost:5173/?run=$RUN_ID"
echo ""
log_info "Press Ctrl+C to stop all components"
echo ""
echo "─────────────────────────────────────────────────────────────────"
echo ""

# Tail all logs
tail_log "world" "$CYAN" "$RUN_DIR/world.log"
tail_log "runner" "$YELLOW" "$RUN_DIR/runner.log"
tail_log "viewer" "$MAGENTA" "$RUN_DIR/viewer.log"

# Wait for any process to exit
wait
