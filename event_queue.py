import asyncio
import queue
from typing import List
from track_events import TrackEvent


class EventBridge:
    def __init__(self):
        self._sync_q: "queue.Queue[TrackEvent]" = queue.Queue()

    def put(self, event: TrackEvent) -> None:
        self._sync_q.put_nowait(event)

    async def get_batch(self, max_batch=8, max_wait_s=1.0) -> List[TrackEvent]:
        loop = asyncio.get_event_loop()
        batch = []
        first = await loop.run_in_executor(None, self._blocking_get, max_wait_s)
        if first is not None:
            batch.append(first)
        while len(batch) < max_batch:
            try:
                batch.append(self._sync_q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _blocking_get(self, timeout):
        try:
            return self._sync_q.get(timeout=timeout)
        except queue.Empty:
            return None