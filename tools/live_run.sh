#!/bin/bash
# Detached live runs of the bobgame scenario (hamlet, the only one).
#
#   tools/live_run.sh start <seconds>            start ./dev.sh detached, auto-stop after <seconds>
#   tools/live_run.sh status                     one-screen health + progress report
#   tools/live_run.sh wait-ticks                 block until the world records ticks (max 3 minutes)
#   tools/live_run.sh wait                       block until the run ends (or 9 minutes pass)
#   tools/live_run.sh stop                       stop the run cleanly (SIGINT, same as Ctrl+C)
#
# The run is started with setsid so it survives the calling shell, and wrapped
# in `timeout -s INT` so it always ends on its own. State lives in
# runs/.live_run.pid and runs/.live_run.out (dev.sh console output).

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The project has exactly one scenario.
CONFIG="hamlet"
PID_FILE="$ROOT/runs/.live_run.pid"
OUT_FILE="$ROOT/runs/.live_run.out"
PORTS=(50051 8765 8766 5173)
MIN_FREE_MB=6000

# What counts as a real error: a structlog line at error/critical level, the
# final line of a Python exception, or an OOM kill. Excluded as harmless: the
# websocket handshake errors caused by dev.sh probing ports with a bare TCP
# connect (EOFError / InvalidMessage).
ERROR_PATTERN='\[(error|critical) *\]|^[A-Za-z_.]*(Error|Exception): |Killed|did not start within'
BENIGN='^EOFError: |InvalidMessage: did not receive a valid HTTP request'

is_alive() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

port_busy() {
    (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

cmd_start() {
    local seconds="${1:?usage: start <seconds>}"
    if ! [[ "$seconds" =~ ^[0-9]+$ ]] || [ "$seconds" -lt 1 ]; then
        echo "REFUSED: '$seconds' is not a number of seconds. Usage: start <seconds> (the only scenario is $CONFIG)."
        return 2
    fi
    if is_alive; then
        echo "REFUSED: a live run is already active (pid $(cat "$PID_FILE")). Run 'status' or 'stop' first."
        return 2
    fi
    for port in "${PORTS[@]}"; do
        if port_busy "$port"; then
            echo "REFUSED: port $port is in use. Something else is running (maybe the user's own ./dev.sh). Do not kill it; report this."
            return 2
        fi
    done
    local free_mb
    free_mb=$(free -m | awk '/^Mem:/ {print $7}')
    if [ "$free_mb" -lt "$MIN_FREE_MB" ]; then
        echo "REFUSED: only ${free_mb} MB memory available, need ${MIN_FREE_MB}. Wait a minute and retry once; report if it persists."
        return 2
    fi
    if [ ! -f "$ROOT/world/configs/$CONFIG.toml" ]; then
        echo "REFUSED: no world/configs/$CONFIG.toml"
        return 2
    fi
    mkdir -p "$ROOT/runs"
    cd "$ROOT" || return 1
    setsid nohup timeout -s INT "$seconds" ./dev.sh "$CONFIG" >"$OUT_FILE" 2>&1 < /dev/null &
    echo $! >"$PID_FILE"
    echo "STARTED pid=$(cat "$PID_FILE") config=$CONFIG auto_stop_after=${seconds}s"
    echo "The world needs about 40 s to load the island. Run 'wait-ticks' next."
}

cmd_status() {
    local state="ENDED"
    is_alive && state="RUNNING"
    echo "state: $state"
    local run_dir
    run_dir="$(readlink -f "$ROOT/runs/latest" 2>/dev/null)"
    echo "run_dir: $run_dir"
    echo "memory_available_mb: $(free -m | awk '/^Mem:/ {print $7}')"
    if [ -s "$run_dir/world/ticks.jsonl.gz" ]; then
        (cd "$ROOT" && python tools/analyze_run.py "$run_dir" 2>&1 \
            | grep -E "^== world|^deaths|^wolf kills|^wolves|^crafts|^placements|^building|^notes")
        local agents_up
        agents_up=$(pgrep -fc "agents.jev_agent" || true)
        echo "agent_processes: $agents_up"
        echo "build_tool_ticks: $(zcat -f "$run_dir"/agents/agent-*/stints.jsonl.gz 2>/dev/null | grep -c '"driver"')"
    else
        echo "ticks: none recorded yet"
    fi
    echo "--- real errors (benign websocket handshake noise filtered out) ---"
    local found=0
    for log in "$run_dir"/world.log "$run_dir"/runner.log "$run_dir"/replay.log "$run_dir"/agents/*.log; do
        [ -f "$log" ] || continue
        local hits
        hits=$(sed 's/\x1b\[[0-9;]*m//g' "$log" \
            | grep -E "$ERROR_PATTERN" \
            | grep -Ev "$BENIGN" | sort | uniq -c | sort -rn | head -5 | cut -c1-220)
        if [ -n "$hits" ]; then
            found=1
            echo "[$(basename "$log")]"
            echo "$hits"
        fi
    done
    [ "$found" -eq 0 ] && echo "none"
    echo "--- last dev.sh console lines ---"
    sed 's/\x1b\[[0-9;]*m//g' "$OUT_FILE" 2>/dev/null | grep "\[dev\]" | tail -4 | cut -c1-200
}

cmd_wait_ticks() {
    local deadline=$((SECONDS + 180))
    while is_alive && [ "$SECONDS" -lt "$deadline" ]; do
        if [ -s "$(readlink -f "$ROOT/runs/latest")/world/ticks.jsonl.gz" ]; then
            echo "TICKING"
            return 0
        fi
        sleep 5
    done
    is_alive && echo "NO TICKS AFTER 3 MINUTES" || echo "ENDED BEFORE TICKING"
    return 1
}

cmd_wait() {
    # Bounded so a single call never exceeds a tool timeout; call again if RUNNING.
    local deadline=$((SECONDS + 540))
    while is_alive && [ "$SECONDS" -lt "$deadline" ]; do
        sleep 5
    done
    is_alive && echo "STILL RUNNING" || echo "ENDED"
}

cmd_stop() {
    if ! is_alive; then
        echo "no live run"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    # timeout forwards SIGINT to dev.sh, whose trap shuts every component down.
    kill -INT "$pid"
    for _ in $(seq 1 30); do
        is_alive || break
        sleep 1
    done
    if is_alive; then
        echo "STOP FAILED: pid $pid still alive after 30 s. Report this; do not use kill -9."
        return 1
    fi
    echo "STOPPED"
}

case "${1:-}" in
    start) shift; cmd_start "$@" ;;
    status) cmd_status ;;
    wait-ticks) cmd_wait_ticks ;;
    wait) cmd_wait ;;
    stop) cmd_stop ;;
    *) sed -n 2,9p "${BASH_SOURCE[0]}"; exit 2 ;;
esac
