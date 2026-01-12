"""Map persistence: save and load generated worlds."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from .config import TerrainConfig
from .objects import ObjectType, PlacedObject

logger = logging.getLogger(__name__)


def save_map(
    path: Path,
    floor: NDArray[np.uint8],
    objects: list[PlacedObject],
    config: TerrainConfig,
) -> None:
    """Save generated map to disk.

    Uses numpy's compressed .npz format for efficient storage.

    Args:
        path: Output path (should end with .npz).
        floor: Floor type array.
        objects: List of placed objects.
        config: Generation configuration used.
    """
    # Convert objects to JSON-serializable format
    objects_data = [
        {
            "x": obj.x,
            "y": obj.y,
            "object_type": obj.object_type.value,
            "object_id": obj.object_id,
        }
        for obj in objects
    ]

    # Metadata
    metadata = {
        "version": 1,
        "seed": config.seed,
        "width": config.width,
        "height": config.height,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    np.savez_compressed(
        path,
        floor=floor,
        objects=json.dumps(objects_data).encode("utf-8"),
        metadata=json.dumps(metadata).encode("utf-8"),
    )

    file_size = path.stat().st_size / (1024 * 1024)
    logger.info(f"Saved map to {path} ({file_size:.1f} MB)")


def load_map(path: Path) -> tuple[NDArray[np.uint8], list[PlacedObject], dict]:
    """Load map from disk.

    Args:
        path: Path to .npz file.

    Returns:
        Tuple of (floor array, list of PlacedObject, metadata dict).

    Raises:
        FileNotFoundError: If file doesn't exist.
        ValueError: If file format is invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Map file not found: {path}")

    data = np.load(path)

    # Load floor array
    if "floor" not in data:
        raise ValueError("Invalid map file: missing 'floor' array")
    floor = data["floor"]

    # Load objects
    if "objects" in data:
        objects_json = data["objects"].tobytes().decode("utf-8")
        objects_data = json.loads(objects_json)
        objects = [
            PlacedObject(
                x=obj["x"],
                y=obj["y"],
                object_type=ObjectType(obj["object_type"]),
                object_id=obj["object_id"],
            )
            for obj in objects_data
        ]
    else:
        objects = []

    # Load metadata
    if "metadata" in data:
        metadata_json = data["metadata"].tobytes().decode("utf-8")
        metadata = json.loads(metadata_json)
    else:
        metadata = {}

    logger.info(f"Loaded map from {path}: {floor.shape[1]}x{floor.shape[0]}")
    return floor, objects, metadata
