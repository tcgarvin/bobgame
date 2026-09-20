"""What a settler can see, hear, say and read.

The view radius, the speech channels and their earshot, conversations, signs
and message boards. Contract: docs/09_conversation_and_reflex.md and
docs/08_building.md ("Signs").
"""

from typing import Mapping

# --- Sight -----------------------------------------------------------------

# How far a settler sees, in tiles (Chebyshev). One definition: the world's
# observation service, the agent's world model, Jev's map and the reflex all
# use this.
VIEW_RADIUS = 8

# --- Speech channels -------------------------------------------------------

LOCAL_CHANNEL = "local"
SHOUT_CHANNEL = "shout"
THOUGHT_CHANNEL = "thought"
# Utterance channel used by `speak` inside a conversation.
CONVERSATION_CHANNEL = "conversation"

# Channels a `SayIntent` may use. `thought` is viewer-only: nobody hears it.
SAY_CHANNELS: frozenset[str] = frozenset(
    {LOCAL_CHANNEL, SHOUT_CHANNEL, THOUGHT_CHANNEL}
)
# Channels that carry in-world sound.
AUDIBLE_CHANNELS: frozenset[str] = frozenset(
    {LOCAL_CHANNEL, SHOUT_CHANNEL, CONVERSATION_CHANNEL}
)

SAY_RADIUS = 10
SHOUT_RADIUS = 60
HEARING_RADIUS_BY_CHANNEL: Mapping[str, int] = {
    LOCAL_CHANNEL: SAY_RADIUS,
    SHOUT_CHANNEL: SHOUT_RADIUS,
    CONVERSATION_CHANNEL: SAY_RADIUS,
}

# --- Conversations ---------------------------------------------------------

# Object type of a conversation, sitting on its anchor tile.
CONVERSATION = "conversation"
# Seats, including the opener.
CONVERSATION_MAX_PARTICIPANTS = 4
# A turn not used within this many ticks counts as a pass.
CONVERSATION_TURN_TICKS = 10
# A conversation that never gained a second participant closes after this.
CONVERSATION_LONELY_TICKS = 20
# A conversation closes after this many `speak` actions.
CONVERSATION_MAX_UTTERANCES = 40
# Characters per line; longer text is truncated.
CONVERSATION_TEXT_LIMIT = 300
# Transcript lines kept in object state.
CONVERSATION_TRANSCRIPT_KEPT = 12
# How long after its last conversation ended a settler cannot be hailed
# (docs/09, section 9). Joining and opening are unaffected.
HAIL_COOLDOWN_TICKS = 60

# `ConverseIntent.action` values.
CONVERSE_OPEN = "open"
CONVERSE_JOIN = "join"
CONVERSE_SPEAK = "speak"
CONVERSE_PASS = "pass"
CONVERSE_LEAVE = "leave"
CONVERSE_HAIL = "hail"
CONVERSE_ACTIONS: frozenset[str] = frozenset(
    {
        CONVERSE_OPEN,
        CONVERSE_JOIN,
        CONVERSE_SPEAK,
        CONVERSE_PASS,
        CONVERSE_LEAVE,
        CONVERSE_HAIL,
    }
)
# `EntityActed.action_type` for every `ConverseIntent`, whatever its action.
CONVERSE_ACTION_TYPE = "converse"
# The word that opens the details of the hailed settler's own `EntityActed`,
# so its agent can tell a seat it never asked for from one it did.
HAILED_DETAIL = "hailed"

# --- Signs -----------------------------------------------------------------

# A sign holds one line. Longer text is refused, never truncated.
SIGN_TEXT_MAX = 80
# Object state keys a placed sign carries.
SIGN_TEXT_KEY = "text"
SIGN_AUTHOR_KEY = "author"
SIGN_TICK_KEY = "tick"
# A sign has one slot, so `WriteNoteIntent.slot` must be this.
SIGN_SLOT = 0

# --- Message boards --------------------------------------------------------

BOARD_SLOTS = 20
NOTE_TITLE_MAX = 60
NOTE_TEXT_MAX = 500
