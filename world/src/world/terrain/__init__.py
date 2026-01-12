"""Procedural terrain generation package.

This package implements noise-based terrain generation for island worlds,
including elevation, moisture, hydrology (rivers/fords), and object placement.
"""

from .config import TerrainConfig
from .generator import (
    GenerationResult,
    generate_and_save_world,
    generate_terrain,
    generate_world,
    load_world,
)
from .persistence import load_map, save_map
from .validation import ValidationResult, validate_terrain

__all__ = [
    "GenerationResult",
    "TerrainConfig",
    "ValidationResult",
    "generate_and_save_world",
    "generate_terrain",
    "generate_world",
    "load_map",
    "load_world",
    "save_map",
    "validate_terrain",
]
