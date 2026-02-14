"""Unit tests for event streaming."""

import asyncio
import json
from datetime import datetime

import pytest

from events import EventBus, EventSubscription, EventType, ResourceEvent

# ==================== EventType tests ====================


class TestEventType:
    """Tests for the EventType enum."""

    def test_created_value(self):
        assert EventType.CREATED.value == "CREATED"

    def test_modified_value(self):
        assert EventType.MODIFIED.value == "MODIFIED"

    def test_deleted_value(self):
        assert EventType.DELETED.value == "DELETED"

    def test_reconciled_value(self):
        assert EventType.RECONCILED.value == "RECONCILED"

    def test_all_members(self):
        assert len(EventType) == 4


# ==================== ResourceEvent tests ====================


class TestResourceEvent:
    """Tests for the ResourceEvent dataclass."""

    @pytest.fixture
    def sample_resource(self):
        return {
            "id": 1,
            "name": "production-pg",
            "resource_type_name": "DatabaseCluster",
            "resource_type_version": "v1",
            "spec": {"engine": "postgres"},
            "status": "ready",
        }

    @pytest.fixture
    def sample_event(self, sample_resource):
        return ResourceEvent(
            event_type=EventType.CREATED,
            resource_id=1,
            resource_name="production-pg",
            resource_type_name="DatabaseCluster",
            resource_type_version="v1",
            resource_data=sample_resource,
            timestamp="2024-01-15T10:30:00Z",
        )

    def test_to_sse_format(self, sample_event):
        sse = sample_event.to_sse()
        lines = sse.split("\n")
        assert lines[0] == "event: CREATED"
        assert lines[1].startswith("data: ")
        # Ends with double newline
        assert sse.endswith("\n\n")

    def test_to_sse_json_valid(self, sample_event):
        sse = sample_event.to_sse()
        data_line = sse.split("\n")[1]
        json_str = data_line[len("data: ") :]
        parsed = json.loads(json_str)
        assert parsed["event_type"] == "CREATED"
        assert parsed["resource_id"] == 1
        assert parsed["resource_name"] == "production-pg"
        assert parsed["resource_type_name"] == "DatabaseCluster"
        assert parsed["resource_type_version"] == "v1"
        assert parsed["timestamp"] == "2024-01-15T10:30:00Z"

    def test_to_sse_datetime_in_resource_data(self, sample_resource):
        """Datetime objects in resource_data are serialized properly."""
        sample_resource["created_at"] = datetime(2024, 1, 15, 10, 30, 0)
        event = ResourceEvent(
            event_type=EventType.MODIFIED,
            resource_id=1,
            resource_name="production-pg",
            resource_type_name="DatabaseCluster",
            resource_type_version="v1",
            resource_data=sample_resource,
            timestamp="2024-01-15T10:35:00Z",
        )
        sse = event.to_sse()
        data_line = sse.split("\n")[1]
        json_str = data_line[len("data: ") :]
        parsed = json.loads(json_str)
        assert parsed["resource_data"]["created_at"] == "2024-01-15T10:30:00"

    def test_from_resource(self, sample_resource):
        event = ResourceEvent.from_resource(EventType.CREATED, sample_resource)
        assert event.event_type == EventType.CREATED
        assert event.resource_id == 1
        assert event.resource_name == "production-pg"
        assert event.resource_type_name == "DatabaseCluster"
        assert event.resource_type_version == "v1"
        assert event.resource_data is sample_resource
        assert event.timestamp.endswith("Z")

    def test_from_resource_timestamp_iso8601(self, sample_resource):
        event = ResourceEvent.from_resource(EventType.CREATED, sample_resource)
        # Should be parseable as ISO 8601 (strip trailing Z)
        datetime.fromisoformat(event.timestamp.rstrip("Z"))


# ==================== EventSubscription tests ====================


@pytest.mark.asyncio
class TestEventSubscription:
    """Tests for the EventSubscription async iterator."""

    async def test_async_iteration(self):
        queue = asyncio.Queue()
        sub = EventSubscription(queue)

        event = ResourceEvent(
            event_type=EventType.CREATED,
            resource_id=1,
            resource_name="test",
            resource_type_name="Test",
            resource_type_version="v1",
            resource_data={},
            timestamp="2024-01-15T10:30:00Z",
        )
        await queue.put(event)
        await queue.put(None)  # Sentinel to stop

        received = []
        async for e in sub:
            received.append(e)

        assert len(received) == 1
        assert received[0] is event

    async def test_filter_fn_applied(self):
        queue = asyncio.Queue()

        def only_created(e):
            return e.event_type == EventType.CREATED

        sub = EventSubscription(queue, filter_fn=only_created)

        created = ResourceEvent(
            event_type=EventType.CREATED,
            resource_id=1,
            resource_name="test",
            resource_type_name="Test",
            resource_type_version="v1",
            resource_data={},
            timestamp="2024-01-15T10:30:00Z",
        )
        modified = ResourceEvent(
            event_type=EventType.MODIFIED,
            resource_id=1,
            resource_name="test",
            resource_type_name="Test",
            resource_type_version="v1",
            resource_data={},
            timestamp="2024-01-15T10:31:00Z",
        )

        await queue.put(modified)
        await queue.put(created)
        await queue.put(None)

        received = []
        async for e in sub:
            received.append(e)

        assert len(received) == 1
        assert received[0].event_type == EventType.CREATED

    async def test_sentinel_stops_iteration(self):
        queue = asyncio.Queue()
        sub = EventSubscription(queue)
        await queue.put(None)

        received = []
        async for e in sub:
            received.append(e)

        assert received == []


# ==================== EventBus tests ====================


@pytest.mark.asyncio
class TestEventBus:
    """Tests for the EventBus pub/sub system."""

    @pytest.fixture
    def bus(self):
        return EventBus(queue_size=16)

    @pytest.fixture
    def sample_event(self):
        return ResourceEvent(
            event_type=EventType.CREATED,
            resource_id=1,
            resource_name="test-resource",
            resource_type_name="DatabaseCluster",
            resource_type_version="v1",
            resource_data={"id": 1, "name": "test-resource"},
            timestamp="2024-01-15T10:30:00Z",
        )

    async def test_publish_no_subscribers(self, bus, sample_event):
        """Publishing with no subscribers does not raise."""
        await bus.publish(sample_event)

    async def test_subscribe_returns_id_and_subscription(self, bus):
        subscriber_id, subscription = await bus.subscribe()
        assert isinstance(subscriber_id, str)
        assert isinstance(subscription, EventSubscription)

    async def test_single_subscriber_receives_event(self, bus, sample_event):
        _, sub = await bus.subscribe()
        await bus.publish(sample_event)

        event = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert event is sample_event

    async def test_multiple_subscribers_all_receive(self, bus, sample_event):
        _, sub1 = await bus.subscribe()
        _, sub2 = await bus.subscribe()

        await bus.publish(sample_event)

        e1 = await asyncio.wait_for(sub1.__anext__(), timeout=1.0)
        e2 = await asyncio.wait_for(sub2.__anext__(), timeout=1.0)
        assert e1 is sample_event
        assert e2 is sample_event

    async def test_unsubscribe_removes_subscriber(self, bus):
        sid, _ = await bus.subscribe()
        assert bus.subscriber_count() == 1

        await bus.unsubscribe(sid)
        assert bus.subscriber_count() == 0

    async def test_unsubscribe_sends_sentinel(self, bus):
        sid, sub = await bus.subscribe()
        await bus.unsubscribe(sid)

        received = []
        async for e in sub:
            received.append(e)

        assert received == []

    async def test_full_queue_drops_event(self, sample_event):
        bus = EventBus(queue_size=1)
        _, sub = await bus.subscribe()

        # Fill the queue
        await bus.publish(sample_event)
        # This should be dropped silently
        await bus.publish(sample_event)

        event = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert event is sample_event

    async def test_subscriber_count(self, bus):
        assert bus.subscriber_count() == 0

        sid1, _ = await bus.subscribe()
        assert bus.subscriber_count() == 1

        sid2, _ = await bus.subscribe()
        assert bus.subscriber_count() == 2

        await bus.unsubscribe(sid1)
        assert bus.subscriber_count() == 1

        await bus.unsubscribe(sid2)
        assert bus.subscriber_count() == 0

    async def test_filtered_subscription(self, bus):
        def only_type(event_type):
            return lambda e: e.event_type == event_type

        _, sub = await bus.subscribe(filter_fn=only_type(EventType.DELETED))

        events = [
            ResourceEvent(
                event_type=EventType.CREATED,
                resource_id=1,
                resource_name="r1",
                resource_type_name="T",
                resource_type_version="v1",
                resource_data={},
                timestamp="t1",
            ),
            ResourceEvent(
                event_type=EventType.DELETED,
                resource_id=2,
                resource_name="r2",
                resource_type_name="T",
                resource_type_version="v1",
                resource_data={},
                timestamp="t2",
            ),
        ]

        for e in events:
            await bus.publish(e)

        received = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
        assert received.event_type == EventType.DELETED
        assert received.resource_id == 2

    async def test_unsubscribe_nonexistent_is_noop(self, bus):
        """Unsubscribing a non-existent ID does not raise."""
        await bus.unsubscribe("nonexistent-id")
        assert bus.subscriber_count() == 0

    async def test_publish_after_unsubscribe(self, bus, sample_event):
        """Publishing after all subscribers removed does not raise."""
        sid, _ = await bus.subscribe()
        await bus.unsubscribe(sid)
        await bus.publish(sample_event)
