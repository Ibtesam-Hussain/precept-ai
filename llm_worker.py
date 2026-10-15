import asyncio
import logging
from typing import Dict, List

from track_events import TrackEvent, EventType
from event_queue import EventBridge
from llm_request import describe_batch_cheap
import storage
import tts_narrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_worker")


class LLMWorker:
    def __init__(self, bridge: EventBridge):
        self.bridge = bridge
        self.cache: Dict[int, str] = {}
        storage.init_db()
        tts_narrator.start()

    async def run_forever(self):
        while True:
            batch = await self.bridge.get_batch(max_batch=8, max_wait_s=3.0)
            if batch:
                await self._process_batch(batch)

    async def _process_batch(self, batch: List[TrackEvent]):
        for e in batch:
            storage.insert_event(e)

        needs_description = [e for e in batch if e.event_type in
                            (EventType.TRACK_STABLE, EventType.TRACK_APPEARANCE_CHANGED)]
        for e in batch:
            if e.event_type == EventType.TRACK_EXITED:
                self.cache.pop(e.track_id, None)

        if not needs_description:
            return

        try:
            results = await describe_batch_cheap(needs_description)
        except Exception as exc:
            logger.warning("LLM call failed, skipping this batch: %s", exc)
            if "429" in str(exc):
                await asyncio.sleep(4)
            return

        for event, description in zip(needs_description, results):
            self.cache[event.track_id] = description
            logger.info("track %s (%s): %s", event.track_id, event.event_type.value, description)

            storage.insert_description(
                track_id=event.track_id,
                event_type=event.event_type.value,
                class_name=event.class_name,
                description=description,
            )
            tts_narrator.speak(description)