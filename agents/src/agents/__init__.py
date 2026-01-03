"""Agent implementations for Bob's World."""

# Lazy imports to avoid RuntimeWarning when running submodules directly
def __getattr__(name: str):
    if name == "RandomAgent":
        from .random_agent import RandomAgent
        return RandomAgent
    if name == "discover_entities":
        from .random_agent import discover_entities
        return discover_entities
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "RandomAgent",
    "discover_entities",
]
