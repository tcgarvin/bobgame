"""A two-layer settler agent: a pydantic-ai planner over a Jev tick executor.

See docs/05_jev_agents_design.md for the contract this package implements.
"""

from .agent import JevAgent, run_agent
from .jevclient import JevClient, JevDecision, TypeSafeJevClient
from .options import Option, TravelState, enumerate_options
from .planner import Planner, describe_world
from .stint import Brief, Stint, StintReport
from .worldmodel import WorldModel

__all__ = [
    "Brief",
    "JevAgent",
    "JevClient",
    "JevDecision",
    "Option",
    "Planner",
    "Stint",
    "StintReport",
    "TravelState",
    "TypeSafeJevClient",
    "WorldModel",
    "describe_world",
    "enumerate_options",
    "run_agent",
]
