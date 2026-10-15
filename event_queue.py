import asyncio
import queue
from track_events import TrackEvent


class EventBridge:
    def __init__(self):
        self._sync_q: "queue.Queue[TrackEvent]" = queue.Queue()

    def put(self, event: TrackEvent) -> None:
        self._sync_q.put_nowait(event)

    async def get_event(self) -> TrackEvent:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_q.get)