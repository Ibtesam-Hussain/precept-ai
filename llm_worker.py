import asyncio
import json
import logging
from dataclasses import asdict
from typing import Dict, List

from track_events import TrackEvent, EventType
from event_queue import EventBridge
import cv2
import base64

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_worker")


class LLMWorker:
    def __init__(self, bridge: EventBridge, event_log_path="events_log.jsonl"):
        self.bridge = bridge
        self.cache: Dict[int, str] = {}
        self.event_log_path = event_log_path

    async def run_forever(self):
        while True:
            batch = await self.bridge.get_batch(max_batch=8, max_wait_s=1.0)
            if batch:
                await self._process_batch(batch)

    async def _process_batch(self, batch: List[TrackEvent]):
        self._log_events(batch)

        needs_description = [e for e in batch if e.event_type in
                              (EventType.TRACK_STABLE, EventType.TRACK_APPEARANCE_CHANGED)]
        for e in batch:
            if e.event_type == EventType.TRACK_EXITED:
                self.cache.pop(e.track_id, None)

        if not needs_description:
            return

        results = await self.describe_batch_cheap(needs_description)
        for event, description in zip(needs_description, results):
            self.cache[event.track_id] = description
            logger.info("track %s: %s", event.track_id, description)

    async def describe_batch_cheap(self, events: List[TrackEvent]) -> List[str]:
        # TODO: replace with a real API call (see below)
        await asyncio.sleep(0)
        return [f"[stub] {e.class_name} track {e.track_id}" for e in events]

    def _log_events(self, batch):
        with open(self.event_log_path, "a") as f:
            for e in batch:
                row = asdict(e)
                row["event_type"] = e.event_type.value
                row.pop("crop", None)   # numpy array isn't JSON-serializable, drop it
                f.write(json.dumps(row) + "\n")

    def _image_block(crop) -> dict:
        _, buf = cv2.imencode(".jpg", crop)
        b64 = base64.b64encode(buf).decode()
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}