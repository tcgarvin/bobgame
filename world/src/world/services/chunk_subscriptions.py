"""Chunk subscription handling shared by the live and replay viewer services.

Both services speak the same chunk protocol: a client subscribes to a set of
chunks (directly or via a viewport), gets `chunk_data` for the new ones and
`chunk_unload` for the ones it dropped.
"""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..chunks import Chunk, ChunkManager
from ..encoding import encode_terrain_base64
from ..exceptions import EntityNotFoundError, ObjectNotFoundError
from ..state import World
from ..viewer_payload import entity_state, object_state

# Sends one JSON-serialisable message to one client.
SendCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class ChunkSubscription:
    """Per-client chunk subscription state."""

    subscribed_chunks: set[tuple[int, int]] = field(default_factory=set)
    chunk_versions: dict[tuple[int, int], int] = field(default_factory=dict)


def viewport_chunks(
    chunk_manager: ChunkManager, message: dict[str, Any]
) -> list[tuple[int, int]]:
    """Chunk coordinates covering the viewport in a `subscribe_viewport`."""
    viewport = message.get("viewport", {})
    return chunk_manager.get_chunks_for_viewport(
        int(viewport.get("x", 0)),
        int(viewport.get("y", 0)),
        int(viewport.get("width", 64)),
        int(viewport.get("height", 48)),
    )


def requested_chunks(message: dict[str, Any]) -> list[tuple[int, int]]:
    """Chunk coordinates listed in a `subscribe_chunks` message."""
    return [
        (int(chunk[0]), int(chunk[1]))
        for chunk in message.get("chunks", [])
        if len(chunk) >= 2
    ]


def chunk_data_message(chunk: Chunk, world: World) -> dict[str, Any]:
    """Full `chunk_data` message for one chunk of `world`."""
    entities = []
    for entity_id in chunk.entities:
        try:
            entities.append(entity_state(world.get_entity(entity_id)))
        except EntityNotFoundError:
            pass  # Entity may have been removed

    objects = []
    for object_id in chunk.objects:
        try:
            objects.append(object_state(world.get_object(object_id)))
        except ObjectNotFoundError:
            pass  # Object may have been removed

    return {
        "type": "chunk_data",
        "chunk_x": chunk.chunk_x,
        "chunk_y": chunk.chunk_y,
        "version": chunk.version,
        "terrain": encode_terrain_base64(chunk.terrain),
        "entities": entities,
        "objects": objects,
    }


async def apply_subscription(
    subscription: ChunkSubscription,
    chunk_manager: ChunkManager,
    world: World,
    chunks: list[tuple[int, int]],
    send: SendCallback,
) -> tuple[int, int]:
    """Move a client to a new chunk subscription.

    Sends `chunk_unload` for dropped chunks and `chunk_data` for new ones.

    Returns:
        (new chunk count, dropped chunk count).
    """
    wanted = set(chunks)
    new_chunks = wanted - subscription.subscribed_chunks
    old_chunks = subscription.subscribed_chunks - wanted

    for chunk_x, chunk_y in old_chunks:
        await send({"type": "chunk_unload", "chunk_x": chunk_x, "chunk_y": chunk_y})
        subscription.chunk_versions.pop((chunk_x, chunk_y), None)

    subscription.subscribed_chunks = wanted

    for chunk_x, chunk_y in new_chunks:
        chunk = chunk_manager.get_chunk(chunk_x, chunk_y)
        if chunk:
            await send(chunk_data_message(chunk, world))
            subscription.chunk_versions[(chunk_x, chunk_y)] = chunk.version

    return (len(new_chunks), len(old_chunks))


async def resend_subscribed_chunks(
    subscription: ChunkSubscription,
    chunk_manager: ChunkManager,
    world: World,
    send: SendCallback,
) -> None:
    """Send `chunk_data` for every chunk the client is subscribed to.

    The replay server uses this after a snapshot, which clears the viewer's
    world, to repopulate the chunks the camera is already looking at.
    """
    for chunk_x, chunk_y in sorted(subscription.subscribed_chunks):
        chunk = chunk_manager.get_chunk(chunk_x, chunk_y)
        if chunk:
            await send(chunk_data_message(chunk, world))
            subscription.chunk_versions[(chunk_x, chunk_y)] = chunk.version
