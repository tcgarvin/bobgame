"""The pause that takes a save (docs/14_new_moon_and_saves.md, section 3).

On a new-moon night the world stops for one tick, `T = N + NEW_MOON_SAVE_OFFSET`,
and waits for every settler to write its own snapshot into
`runs/<run>/saves/tick-<T>/agents/`. When they all have, the world writes its
half and carries on with tick `T` as usual. On a timeout the save is abandoned
and the run continues: a run is never blocked by a save.

The coordinator is kept out of `TickLoop`'s body so it can be tested with a
tmp_path and a fake agent dropping files.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import structlog

from .moon import (
    NEW_MOON_SAVE_OFFSET,
    is_new_moon_day,
    save_tick_of_day,
)
from .snapshot import (
    abandon_save_dir,
    agent_snapshot_path,
    prepare_save_dir,
    save_dir_for,
    write_world_snapshot,
)
from .state import WOLF_ENTITY_TYPE, World
from .wolves import WolfSimulator

logger = structlog.get_logger()

__all__ = ["NEW_MOON_SAVE_OFFSET", "SaveCoordinator", "SaveSettings"]

# How often the world looks for the settlers' files while it waits.
POLL_INTERVAL_S = 0.25


@dataclass(frozen=True)
class SaveSettings:
    """Everything a save needs that does not come from the world itself.

    Attributes:
        run_dir: Where the run is recorded; None disables saving entirely.
        run_id: Run id, written into the save.
        config: The full run config, written into the save.
        config_name: Its name, e.g. "hamlet".
        config_path: Its path relative to the project root.
        map_path: Map path relative to the project root, "" when there is none.
        map_sha256: sha256 of that map file, "" when there is none.
        enabled: The `save_on_new_moon` setting.
        wait_seconds: How long to wait for the settlers' files.
        suppress_tick: A tick that must never take a save. On a resume this is
            the tick being resumed, whose save already exists.
        poll_interval_s: How often to look for the files while waiting.
    """

    run_dir: Path | None = None
    run_id: str = ""
    config: Mapping[str, Any] = field(default_factory=dict)
    config_name: str = ""
    config_path: str = ""
    map_path: str = ""
    map_sha256: str = ""
    enabled: bool = True
    wait_seconds: int = 180
    suppress_tick: int = -1
    poll_interval_s: float = POLL_INTERVAL_S


class SaveCoordinator:
    """Decides when a save is due, waits for the settlers and writes it."""

    def __init__(
        self,
        world: World,
        wolf_simulator: WolfSimulator,
        settings: SaveSettings = SaveSettings(),
    ):
        self.world = world
        self.wolf_simulator = wolf_simulator
        self.settings = settings

    @property
    def run_dir(self) -> Path | None:
        """Where saves are written; None when this world never saves."""
        return self.settings.run_dir

    @property
    def wait_seconds(self) -> int:
        """How long the world waits for the settlers' files."""
        return self.settings.wait_seconds

    # --- when ------------------------------------------------------------

    def pending_save_tick(self) -> int:
        """The tick a save is due on right now, or 0 when none is.

        A save is due at `night_start + NEW_MOON_SAVE_OFFSET` of a new-moon
        night, when saving is on and the run is being recorded.
        """
        if not self.settings.enabled or self.settings.run_dir is None:
            return 0
        clock = self.world.clock
        if not is_new_moon_day(clock.day, self.world.new_moon_every_days):
            return 0
        if clock.tick_of_day != save_tick_of_day(clock.day_length):
            return 0
        if self.world.tick == self.settings.suppress_tick:
            logger.info("save_skipped_on_resume", tick=self.world.tick)
            return 0
        return self.world.tick

    def expected_entities(self) -> list[str]:
        """The settlers that must write a snapshot file, in id order."""
        return sorted(
            entity_id
            for entity_id, entity in self.world.all_entities().items()
            if entity.entity_type != WOLF_ENTITY_TYPE
        )

    # --- doing -----------------------------------------------------------

    def prepare(self, tick: int) -> Path | None:
        """Create the save directory before observation `tick` goes out.

        Returns the directory, or None when it could not be created (the save
        is then simply not taken).
        """
        if self.settings.run_dir is None:
            return None
        try:
            return prepare_save_dir(self.settings.run_dir, tick)
        except OSError as exc:
            logger.error(
                "save_dir_failed",
                tick=tick,
                run_dir=str(self.settings.run_dir),
                error=str(exc),
            )
            return None

    async def wait_and_write(self, tick: int) -> bool:
        """Wait for every settler's file, then write the world's half.

        Returns True when the save is complete. On a timeout it logs
        `save_abandoned` at error level, names the settlers that did not
        report, renames the directory aside and returns False.
        """
        if self.settings.run_dir is None:
            return False
        save_dir = save_dir_for(self.settings.run_dir, tick)
        expected = self.expected_entities()

        missing = await self._await_agent_files(save_dir, expected)
        if missing:
            logger.error(
                "save_abandoned",
                tick=tick,
                waited_seconds=self.settings.wait_seconds,
                missing=",".join(missing),
                path=str(save_dir),
            )
            self._abandon(save_dir)
            return False

        try:
            write_world_snapshot(
                self.world,
                save_dir=save_dir,
                run_id=self.settings.run_id,
                config=self.settings.config,
                config_name=self.settings.config_name,
                config_path=self.settings.config_path,
                map_path=self.settings.map_path,
                map_sha256=self.settings.map_sha256,
                wolf_simulator=self.wolf_simulator,
            )
        except OSError as exc:
            logger.error("save_write_failed", tick=tick, error=str(exc))
            self._abandon(save_dir)
            return False
        return True

    # --- internals -------------------------------------------------------

    async def _await_agent_files(
        self, save_dir: Path, expected: list[str]
    ) -> list[str]:
        """Poll until every settler's file is there; return what is missing."""
        loop = asyncio.get_running_loop()
        give_up_at = loop.time() + self.settings.wait_seconds
        while True:
            missing = [
                entity_id
                for entity_id in expected
                if not agent_snapshot_path(save_dir, entity_id).is_file()
            ]
            if not missing:
                return []
            if loop.time() >= give_up_at:
                return missing
            await asyncio.sleep(self.settings.poll_interval_s)

    def _abandon(self, save_dir: Path) -> None:
        """Move an unfinished save aside, keeping whatever the agents wrote."""
        if not save_dir.is_dir():
            return
        try:
            moved = abandon_save_dir(save_dir)
        except OSError as exc:
            logger.error("save_abandon_failed", path=str(save_dir), error=str(exc))
            return
        logger.info("save_abandoned_kept", path=str(moved))
