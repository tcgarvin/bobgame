"""A two-layer settler agent: a pydantic-ai planner over a Jev tick executor.

See docs/05_jev_agents_design.md for the contract this package implements.
"""

from .agent import JevAgent, run_agent
from .conversation import ConversationReport, ConversationSession, ModelConverser
from .jevclient import JevClient, JevDecision, TypeSafeJevClient
from .options import Option, TravelState, enumerate_options
from .planner import Planner, describe_world
from .reflex import ReflexBrief, ReflexStore, ReflexWatch
from .stint import Brief, Stint, StintReport
from .tracelog import AgentTrace, JsonlGzWriter, resolve_log_root
from .worldmodel import WorldModel

__all__ = [
    "AgentTrace",
    "Brief",
    "ConversationReport",
    "ConversationSession",
    "JevAgent",
    "JevClient",
    "JevDecision",
    "JsonlGzWriter",
    "ModelConverser",
    "Option",
    "Planner",
    "ReflexBrief",
    "ReflexStore",
    "ReflexWatch",
    "Stint",
    "StintReport",
    "TravelState",
    "TypeSafeJevClient",
    "WorldModel",
    "describe_world",
    "enumerate_options",
    "resolve_log_root",
    "run_agent",
]
