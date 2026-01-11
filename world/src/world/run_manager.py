"""Run management for simulation logging."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunMetadata:
    """Metadata for a simulation run."""

    run_id: str
    started_at: str
    config_name: str
    world_width: int
    world_height: int
    tick_duration_ms: int
    entity_ids: list[str] = field(default_factory=list)
    object_ids: list[str] = field(default_factory=list)
    schema_version: int = 1


class RunManager:
    """Manages simulation run directories and metadata."""

    def __init__(self, base_dir: Path | str = "runs"):
        self.base_dir = Path(base_dir)
        self._run_id: str | None = None
        self._run_dir: Path | None = None
        self._metadata: RunMetadata | None = None

    @property
    def run_id(self) -> str | None:
        """Current run ID, or None if not started."""
        return self._run_id

    @property
    def run_dir(self) -> Path | None:
        """Current run directory, or None if not started."""
        return self._run_dir

    def start_run(
        self,
        config_name: str,
        world_width: int,
        world_height: int,
        tick_duration_ms: int,
        entity_ids: list[str],
        object_ids: list[str],
    ) -> str:
        """Start a new run and create the run directory.

        Args:
            config_name: Name of the configuration used
            world_width: World width in tiles
            world_height: World height in tiles
            tick_duration_ms: Tick duration in milliseconds
            entity_ids: List of entity IDs in the world
            object_ids: List of object IDs in the world

        Returns:
            The generated run_id
        """
        self._run_id = self._generate_run_id()
        self._run_dir = self.base_dir / self._run_id
        self._run_dir.mkdir(parents=True, exist_ok=True)

        self._metadata = RunMetadata(
            run_id=self._run_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            config_name=config_name,
            world_width=world_width,
            world_height=world_height,
            tick_duration_ms=tick_duration_ms,
            entity_ids=entity_ids,
            object_ids=object_ids,
        )

        self._write_metadata()
        return self._run_id

    def end_run(self, final_tick: int) -> None:
        """Mark the run as ended and update metadata.

        Args:
            final_tick: The last tick number processed
        """
        if self._run_dir is None or self._metadata is None:
            return

        # Update metadata with end information
        meta_path = self._run_dir / "meta.json"
        with open(meta_path) as f:
            meta_dict = json.load(f)

        meta_dict["ended_at"] = datetime.now(timezone.utc).isoformat()
        meta_dict["final_tick"] = final_tick

        with open(meta_path, "w") as f:
            json.dump(meta_dict, f, indent=2)

    def _generate_run_id(self) -> str:
        """Generate a unique run ID.

        Format: YYYYMMDD_HHMMSS_<short-uuid>
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_uuid = uuid.uuid4().hex[:8]
        return f"{timestamp}_{short_uuid}"

    def _write_metadata(self) -> None:
        """Write metadata to meta.json."""
        if self._run_dir is None or self._metadata is None:
            return

        meta_path = self._run_dir / "meta.json"
        meta_dict = {
            "run_id": self._metadata.run_id,
            "started_at": self._metadata.started_at,
            "config_name": self._metadata.config_name,
            "world_width": self._metadata.world_width,
            "world_height": self._metadata.world_height,
            "tick_duration_ms": self._metadata.tick_duration_ms,
            "entity_ids": self._metadata.entity_ids,
            "object_ids": self._metadata.object_ids,
            "schema_version": self._metadata.schema_version,
        }

        with open(meta_path, "w") as f:
            json.dump(meta_dict, f, indent=2)
