"""Command-line interface for terrain generation."""

import argparse
import logging
import sys
import time
from pathlib import Path


def main() -> None:
    """CLI entry point for terrain generation."""
    parser = argparse.ArgumentParser(
        description="Generate procedural terrain for Bob's World"
    )
    parser.add_argument(
        "--width", type=int, default=4000, help="World width (default: 4000)"
    )
    parser.add_argument(
        "--height", type=int, default=4000, help="World height (default: 4000)"
    )
    parser.add_argument(
        "--seed", type=int, default=12345, help="Random seed (default: 12345)"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="saves/island.npz",
        help="Output path (default: saves/island.npz)",
    )
    parser.add_argument(
        "--debug-images",
        type=str,
        default=None,
        help="Directory to save debug images (optional)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Import here to avoid slow startup for --help
    from .config import TerrainConfig
    from .generator import generate_terrain
    from .persistence import save_map

    # Resolve output path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        # Relative to project root
        project_root = Path(__file__).parent.parent.parent.parent.parent
        output_path = project_root / args.output

    print(f"Generating {args.width}x{args.height} terrain with seed {args.seed}")
    print(f"Output: {output_path}")
    print()

    config = TerrainConfig(
        seed=args.seed,
        width=args.width,
        height=args.height,
        debug_output_dir=args.debug_images,
    )

    start_time = time.time()
    result = generate_terrain(config)
    gen_time = time.time() - start_time

    print()
    print(f"Generation complete in {gen_time:.1f}s")

    # Save to disk
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_map(output_path, result.floor, result.objects, config)

    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()
