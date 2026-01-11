"""Parquet logging for simulation replay."""

from pathlib import Path
from typing import Mapping

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from .foraging import CollectResult, EatResult, ObjectChange
from .movement import MoveResult
from .state import Entity, World, WorldObject
from .tick import TickResult

logger = structlog.get_logger()


# Parquet schemas for each table
TICK_SCHEMA = pa.schema([
    ("tick_id", pa.int32()),
    ("start_time_ms", pa.int64()),
    ("deadline_ms", pa.int64()),
    ("duration_ms", pa.float64()),
])

ENTITY_STATE_SCHEMA = pa.schema([
    ("tick_id", pa.int32()),
    ("entity_id", pa.string()),
    ("x", pa.int32()),
    ("y", pa.int32()),
    ("entity_type", pa.string()),
    ("inventory_json", pa.string()),  # JSON serialized inventory
])

ACTION_SCHEMA = pa.schema([
    ("tick_id", pa.int32()),
    ("action_type", pa.string()),  # "move", "collect", "eat"
    ("entity_id", pa.string()),
    ("success", pa.bool_()),
    ("from_x", pa.int32()),
    ("from_y", pa.int32()),
    ("to_x", pa.int32()),
    ("to_y", pa.int32()),
    ("object_id", pa.string()),
    ("item_type", pa.string()),
    ("amount", pa.int32()),
    ("failure_reason", pa.string()),
])

OBJECT_STATE_SCHEMA = pa.schema([
    ("tick_id", pa.int32()),
    ("object_id", pa.string()),
    ("x", pa.int32()),
    ("y", pa.int32()),
    ("object_type", pa.string()),
    ("state_json", pa.string()),  # JSON serialized state
])


class LogWriter:
    """Writes simulation data to Parquet files.

    Accumulates data in memory and writes to Parquet files on flush or close.
    """

    def __init__(self, run_dir: Path, buffer_size: int = 100):
        """Initialize LogWriter.

        Args:
            run_dir: Directory to write Parquet files to
            buffer_size: Number of ticks to buffer before writing
        """
        self.run_dir = run_dir
        self.buffer_size = buffer_size

        # Accumulation buffers
        self._tick_data: list[dict] = []
        self._entity_data: list[dict] = []
        self._action_data: list[dict] = []
        self._object_data: list[dict] = []

        # Track if files have been written (for append mode)
        self._files_exist = False

    def log_tick(
        self,
        tick_id: int,
        start_time_ms: int,
        deadline_ms: int,
        result: TickResult,
        entities: Mapping[str, Entity],
        objects: Mapping[str, WorldObject],
    ) -> None:
        """Log a completed tick.

        Args:
            tick_id: The tick number
            start_time_ms: When the tick started (unix ms)
            deadline_ms: Intent deadline (unix ms)
            result: The TickResult with action outcomes
            entities: Current entity states after tick
            objects: Current object states after tick
        """
        # Tick timing
        self._tick_data.append({
            "tick_id": tick_id,
            "start_time_ms": start_time_ms,
            "deadline_ms": deadline_ms,
            "duration_ms": result.duration_ms,
        })

        # Entity states
        for entity_id, entity in entities.items():
            inventory_json = self._serialize_inventory(entity)
            self._entity_data.append({
                "tick_id": tick_id,
                "entity_id": entity_id,
                "x": entity.position.x,
                "y": entity.position.y,
                "entity_type": entity.entity_type,
                "inventory_json": inventory_json,
            })

        # Actions - moves
        for move in result.move_results:
            self._action_data.append({
                "tick_id": tick_id,
                "action_type": "move",
                "entity_id": move.entity_id,
                "success": move.success,
                "from_x": move.from_pos.x,
                "from_y": move.from_pos.y,
                "to_x": move.to_pos.x,
                "to_y": move.to_pos.y,
                "object_id": None,
                "item_type": None,
                "amount": 0,
                "failure_reason": move.failure_reason,
            })

        # Actions - collects
        for collect in result.collect_results:
            self._action_data.append({
                "tick_id": tick_id,
                "action_type": "collect",
                "entity_id": collect.entity_id,
                "success": collect.success,
                "from_x": 0,
                "from_y": 0,
                "to_x": 0,
                "to_y": 0,
                "object_id": collect.object_id,
                "item_type": collect.item_type,
                "amount": 1 if collect.success else 0,
                "failure_reason": collect.failure_reason,
            })

        # Actions - eats
        for eat in result.eat_results:
            self._action_data.append({
                "tick_id": tick_id,
                "action_type": "eat",
                "entity_id": eat.entity_id,
                "success": eat.success,
                "from_x": 0,
                "from_y": 0,
                "to_x": 0,
                "to_y": 0,
                "object_id": None,
                "item_type": eat.item_type,
                "amount": eat.amount,
                "failure_reason": eat.failure_reason,
            })

        # Object states
        for object_id, obj in objects.items():
            state_json = self._serialize_object_state(obj)
            self._object_data.append({
                "tick_id": tick_id,
                "object_id": object_id,
                "x": obj.position.x,
                "y": obj.position.y,
                "object_type": obj.object_type,
                "state_json": state_json,
            })

        # Auto-flush if buffer is full
        if len(self._tick_data) >= self.buffer_size:
            self.flush()

    def flush(self) -> None:
        """Write buffered data to Parquet files."""
        if not self._tick_data:
            return

        self._write_parquet("ticks.parquet", TICK_SCHEMA, self._tick_data)
        self._write_parquet("entity_state.parquet", ENTITY_STATE_SCHEMA, self._entity_data)
        self._write_parquet("actions.parquet", ACTION_SCHEMA, self._action_data)
        self._write_parquet("objects.parquet", OBJECT_STATE_SCHEMA, self._object_data)

        # Clear buffers
        self._tick_data.clear()
        self._entity_data.clear()
        self._action_data.clear()
        self._object_data.clear()

        self._files_exist = True
        logger.debug("log_flushed", run_dir=str(self.run_dir))

    def close(self) -> None:
        """Flush remaining data and finalize files."""
        self.flush()
        logger.info("log_writer_closed", run_dir=str(self.run_dir))

    def _write_parquet(
        self,
        filename: str,
        schema: pa.Schema,
        data: list[dict],
    ) -> None:
        """Write data to a Parquet file, appending if it exists."""
        if not data:
            return

        filepath = self.run_dir / filename
        table = pa.Table.from_pylist(data, schema=schema)

        if self._files_exist and filepath.exists():
            # Append to existing file by reading, concatenating, and rewriting
            existing = pq.read_table(filepath)
            table = pa.concat_tables([existing, table])

        pq.write_table(table, filepath)

    def _serialize_inventory(self, entity: Entity) -> str:
        """Serialize entity inventory to JSON string."""
        import json
        return json.dumps(dict(entity.inventory.items))

    def _serialize_object_state(self, obj: WorldObject) -> str:
        """Serialize object state to JSON string."""
        import json
        return json.dumps(dict(obj.state))
