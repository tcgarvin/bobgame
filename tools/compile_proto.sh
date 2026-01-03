#!/bin/bash
# Compile protobuf definitions for all Python projects

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PROTO_DIR="$PROJECT_ROOT/proto"

echo "Compiling protobuf files..."

# Compile for world
echo "  -> world/"
cd "$PROJECT_ROOT/world"
uv run python -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out=src/world \
    --grpc_python_out=src/world \
    --pyi_out=src/world \
    "$PROTO_DIR/world.proto"

# Compile for runner
echo "  -> runner/"
cd "$PROJECT_ROOT/runner"
uv run python -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out=src/runner \
    --grpc_python_out=src/runner \
    --pyi_out=src/runner \
    "$PROTO_DIR/world.proto"

# Compile for agents
echo "  -> agents/"
cd "$PROJECT_ROOT/agents"
uv run python -m grpc_tools.protoc \
    -I"$PROTO_DIR" \
    --python_out=src/agents \
    --grpc_python_out=src/agents \
    --pyi_out=src/agents \
    "$PROTO_DIR/world.proto"

echo "Done!"
