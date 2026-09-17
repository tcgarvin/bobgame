#!/bin/bash
#
# Replay a recorded bobgame run.
#
# Usage:
#   ./replay.sh [run_id]
#
# Arguments:
#   run_id - Optional run id under runs/ (default: the target of runs/latest)
#
# Starts only the replay server (WebSocket :8766) and the viewer dev server,
# then prints the deep link that opens the run.
#
# See docs/07_replay.md for the recording format and replay protocol.
#
# Press Ctrl+C to stop both components.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNS_DIR="$SCRIPT_DIR/runs"
PIDS=()

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[replay]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[replay]${NC} $1"
}

log_error() {
    echo -e "${RED}[replay]${NC} $1"
}

for arg in "$@"; do
    case $arg in
        --help|-h)
            echo "Usage: $0 [run_id]"
            echo ""
            echo "Arguments:"
            echo "  run_id  Run directory name under runs/ (default: runs/latest)"
            echo ""
            echo "Starts:"
            echo "  - Replay server (WebSocket :8766, serves runs/)"
            echo "  - Viewer (http://localhost:5173)"
            echo ""
            echo "Available runs:"
            if [ -d "$RUNS_DIR" ]; then
                for run in "$RUNS_DIR"/*/; do
                    name="$(basename "$run")"
                    # Skip the "latest" symlink; it aliases one of the others.
                    [ "$name" = "latest" ] && continue
                    echo "  - $name"
                done
            fi
            exit 0
            ;;
    esac
done

# Cleanup function - kill all spawned processes and their children
cleanup() {
    echo ""
    log_info "Shutting down..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    sleep 1
    pkill -TERM -P $$ 2>/dev/null || true
    sleep 0.5
    pkill -9 -P $$ 2>/dev/null || true
    log_info "Stopped"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

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

# Resolve the run id
RUN_ID="${1:-}"
if [ -z "$RUN_ID" ]; then
    if [ ! -L "$RUNS_DIR/latest" ]; then
        log_error "No run id given and $RUNS_DIR/latest does not exist."
        log_error "Run ./dev.sh first, or pass a run id: $0 <run_id>"
        exit 1
    fi
    RUN_ID="$(basename "$(readlink "$RUNS_DIR/latest")")"
fi

RUN_DIR="$RUNS_DIR/$RUN_ID"
if [ ! -d "$RUN_DIR" ]; then
    log_error "No such run: $RUN_DIR"
    exit 1
fi
log_info "Replaying run $RUN_ID"

# Start Replay Server
cd "$SCRIPT_DIR/world"
uv run python -m world.replay \
    --runs-dir "$RUNS_DIR" \
    --port 8766 \
    > "$RUN_DIR/replay.log" 2>&1 &
REPLAY_PID=$!
PIDS+=($REPLAY_PID)
log_info "Replay Server started (PID: $REPLAY_PID)"
wait_for_port 8766 "Replay WebSocket" 20 || {
    log_error "See $RUN_DIR/replay.log"
    exit 1
}

# Start Viewer Dev Server
cd "$SCRIPT_DIR/viewer"
npm run dev > "$RUN_DIR/viewer-replay.log" 2>&1 &
VIEWER_PID=$!
PIDS+=($VIEWER_PID)
log_info "Viewer started (PID: $VIEWER_PID)"
wait_for_port 5173 "Viewer" || exit 1

echo ""
log_info "Replay ready!"
echo ""
echo -e "  ${CYAN}Replay Server${NC}: ws://localhost:8766"
echo -e "  ${MAGENTA}Viewer${NC}:        http://localhost:5173"
echo ""
echo -e "  ${BLUE}Run${NC}:           $RUN_ID"
echo -e "  ${BLUE}Open${NC}:          http://localhost:5173/?run=$RUN_ID"
echo ""
log_info "Press Ctrl+C to stop"
echo ""
echo "─────────────────────────────────────────────────────────────────"
echo ""

tail -f "$RUN_DIR/replay.log" 2>/dev/null | while IFS= read -r line; do
    echo -e "${CYAN}[replay]${NC} $line"
done &
PIDS+=($!)

wait
