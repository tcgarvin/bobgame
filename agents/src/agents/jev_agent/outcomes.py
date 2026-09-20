"""Reading the world's answer to one submitted intent.

`AgentBridge.direct_action` renders the world's own action event as
`"<what> -> <type> ok: <detail>"`, `"<what> -> <type> failed: <reason>"`, or
`"<what> -> submitted"` when no event came back. Several callers need to know
which of those they are looking at, so the reading of it lives in one place
rather than in each of them.
"""

from __future__ import annotations

# The world's wording for a finished craft, in the action event's details.
CRAFTED_DETAIL = "crafted "


def action_succeeded(outcome: str) -> bool:
    """Whether a direct-action result line reports a successful world action.

    `direct_action` renders the world's own event as `<what> -> <type> ok: ...`
    or `<what> -> <type> failed: ...`, and `-> submitted` when no event came
    back at all.
    """
    return " ok:" in outcome
