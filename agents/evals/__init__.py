"""Live functional evaluation of Jev: real API calls, run on demand.

Kept out of `tests/` (and out of `testpaths`) on purpose: these cost money and
need the network, so `uv run pytest -q` must never pick them up.
"""
