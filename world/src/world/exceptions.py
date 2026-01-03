"""Custom exceptions for the world simulation."""


class WorldError(Exception):
    """Base exception for world errors."""

    pass


class InvalidMoveError(WorldError):
    """Raised when a move is invalid."""

    pass


class EntityNotFoundError(WorldError):
    """Raised when entity is not found."""

    pass


class EntityAlreadyExistsError(WorldError):
    """Raised when trying to add an entity that already exists."""

    pass


class PositionOccupiedError(WorldError):
    """Raised when trying to place an entity on an occupied position."""

    pass


class TickDeadlineError(WorldError):
    """Raised when intent submitted after deadline."""

    pass
