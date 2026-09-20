"""The day clock and the new moon (docs/14_new_moon_and_saves.md, section 1)."""

# --- The day ---------------------------------------------------------------

DEFAULT_DAY_LENGTH_TICKS = 300
# The first two thirds of a day are light; the last third is night. Kept as a
# fraction, not a tick count, because the day length is world config.
NIGHT_START_NUMERATOR = 2
NIGHT_START_DENOMINATOR = 3

# --- The new moon ----------------------------------------------------------

# Ticks from the forced sleep during which nothing wakes anyone.
NEW_MOON_STILL_TICKS = 6
# The save is taken this many ticks into the still window.
NEW_MOON_SAVE_OFFSET = 3
# What the forced sleep and a refused wake are called in events.
NEW_MOON_SLEEP_REASON = "new moon"
NEW_MOON_WAKE_REFUSAL = "new moon"
# End reason recorded when a conversation is closed by the new moon.
NEW_MOON_CONVERSATION_REASON = "new_moon"


def night_start_tick(day_length: int) -> int:
    """The tick of day that night begins on."""
    return day_length * NIGHT_START_NUMERATOR // NIGHT_START_DENOMINATOR


def night_start_fraction() -> float:
    """The same boundary as a fraction of the day, for consumers that need one."""
    return NIGHT_START_NUMERATOR / NIGHT_START_DENOMINATOR
