"""
Event Streaming - In-memory pub/sub for real-time resource events.

Provides Server-Sent Events (SSE) watch semantics similar to
the Kubernetes watch API.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> str:
    """JSON serializer for objects not handled by default json encoder."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class EventType(Enum):
    """Types of resource events."""

    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RECONCILED = "RECONCILED"


@dataclass
class ResourceEvent:
    """Event emitted when a resource changes."""

    event_type: EventType
    resource_id: int
    resource_name: str
    resource_type_name: str
    resource_type_version: str
    resource_data: Dict[str, Any]
    timestamp: str

    def to_sse(self) -> str:
        """
        Format the event as an SSE message.

        Returns:
            SSE-formatted string with event type and JSON data lines.
        """
        data = {
            "event_type": self.event_type.value,
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "resource_type_name": self.resource_type_name,
            "resource_type_version": self.resource_type_version,
            "resource_data": self.resource_data,
            "timestamp": self.timestamp,
        }
        json_data = json.dumps(data, default=_json_default)
        return f"event: {self.event_type.value}\ndata: {json_data}\n\n"

    @classmethod
    def from_resource(
        cls,
        event_type: EventType,
        resource: Dict[str, Any],
    ) -> "ResourceEvent":
        """
        Create an event from a resource dict.

        Args:
            event_type: The type of event.
            resource: Resource dict from the database.

        Returns:
            A new ResourceEvent instance.
        """
        return cls(
            event_type=event_type,
            resource_id=resource["id"],
            resource_name=resource["name"],
            resource_type_name=resource["resource_type_name"],
            resource_type_version=resource["resource_type_version"],
            resource_data=resource,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )


class EventSubscription:
    """
    Async iterator for consuming events from a subscription.

    Reads events from a queue, applying an optional filter function.
    A ``None`` sentinel value stops iteration.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        filter_fn: Optional[Callable[["ResourceEvent"], bool]] = None,
    ):
        self._queue = queue
        self._filter_fn = filter_fn

    def __aiter__(self) -> AsyncIterator["ResourceEvent"]:
        return self

    async def __anext__(self) -> "ResourceEvent":
        while True:
            event = await self._queue.get()

            if event is None:
                raise StopAsyncIteration

            if self._filter_fn is None or self._filter_fn(event):
                return event


class EventBus:
    """
    In-memory pub/sub event bus for resource events.

    Maintains an ``asyncio.Queue`` per subscriber and publishes events
    non-blocking.  Full queues cause events to be silently dropped to
    prevent back-pressure on publishers.
    """

    def __init__(self, queue_size: int = 256):
        self._queue_size = queue_size
        self._subscribers: Dict[str, asyncio.Queue] = {}
        self._lock = asyncio.Lock()

    async def publish(self, event: ResourceEvent) -> None:
        """
        Publish an event to all subscribers (non-blocking).

        Events are dropped for subscribers whose queues are full.

        Args:
            event: The event to publish.
        """
        async with self._lock:
            subscribers = list(self._subscribers.items())

        for subscriber_id, queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    f"Dropped event for subscriber {subscriber_id}: queue full"
                )

    async def subscribe(
        self,
        filter_fn: Optional[Callable[[ResourceEvent], bool]] = None,
    ) -> Tuple[str, EventSubscription]:
        """
        Subscribe to events.

        Args:
            filter_fn: Optional predicate applied to each event.
                Only events for which it returns ``True`` are yielded.

        Returns:
            A tuple of ``(subscriber_id, EventSubscription)``.
        """
        subscriber_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)

        async with self._lock:
            self._subscribers[subscriber_id] = queue

        logger.info(f"New event subscriber: {subscriber_id}")
        return subscriber_id, EventSubscription(queue, filter_fn)

    async def unsubscribe(self, subscriber_id: str) -> None:
        """
        Remove a subscriber and clean up its queue.

        Sends a ``None`` sentinel so that the subscription's async
        iterator terminates gracefully.

        Args:
            subscriber_id: The ID returned by :meth:`subscribe`.
        """
        async with self._lock:
            queue = self._subscribers.pop(subscriber_id, None)

        if queue is not None:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
            logger.info(f"Unsubscribed: {subscriber_id}")

    def subscriber_count(self) -> int:
        """Return the current number of subscribers."""
        return len(self._subscribers)
