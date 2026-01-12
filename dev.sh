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
#   - Runner (manages agent processes with auto-restart)
#   - Viewer dev server
#
# Logs are written to logs/ directory:
#   logs/world.log
#   logs/viewer.log
#   logs/runner.log
#   logs/agent-*.log (managed by runner)
#
# Press Ctrl+C to stop all components
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
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
            echo "  - Simple agents (alice and bob)"
            echo "  - Viewer (http://localhost:5173)"
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

# Cleanup function - kill all spawned processes
cleanup() {
    echo ""
    log_info "Shutting down all components..."

    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Stopping process $pid"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done

    # Wait a moment for graceful shutdown
    sleep 1

    # Force kill any remaining processes
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Force killing process $pid"
            kill -9 "$pid" 2>/dev/null || true
        fi
    done

    log_info "All components stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM EXIT

# Create logs directory
mkdir -p "$LOG_DIR"

# Clear old logs
log_info "Clearing old logs..."
rm -f "$LOG_DIR"/*.log

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
    > "$LOG_DIR/world.log" 2>&1 &
WORLD_PID=$!
PIDS+=($WORLD_PID)
log_info "World Server started (PID: $WORLD_PID)"

# If generating terrain, stream the log while waiting
if [ "$NEEDS_GENERATION" = true ]; then
    # Start tailing log in background
    tail -f "$LOG_DIR/world.log" 2>/dev/null | while IFS= read -r line; do
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

# Start Runner (manages all agents)
log_info "Starting Agent Runner..."
cd "$SCRIPT_DIR/runner"
uv run python -m runner \
    --config "$SCRIPT_DIR/runner/configs/foraging.toml" \
    --log-dir "$LOG_DIR" \
    > "$LOG_DIR/runner.log" 2>&1 &
RUNNER_PID=$!
PIDS+=($RUNNER_PID)
log_info "Agent Runner started (PID: $RUNNER_PID)"

# Give runner a moment to spawn agents
sleep 2

# Start Viewer Dev Server
log_info "Starting Viewer..."
cd "$SCRIPT_DIR/viewer"
npm run dev > "$LOG_DIR/viewer.log" 2>&1 &
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
echo -e "  ${YELLOW}Runner${NC}:        Managing agents (with auto-restart)"
echo -e "  ${MAGENTA}Viewer${NC}:        http://localhost:5173"
echo ""
echo -e "  ${BLUE}Logs${NC}:          $LOG_DIR/"
echo ""
log_info "Press Ctrl+C to stop all components"
echo ""
echo "─────────────────────────────────────────────────────────────────"
echo ""

# Tail all logs
tail_log "world" "$CYAN" "$LOG_DIR/world.log"
tail_log "runner" "$YELLOW" "$LOG_DIR/runner.log"
tail_log "viewer" "$MAGENTA" "$LOG_DIR/viewer.log"

# Wait for any process to exit
wait
