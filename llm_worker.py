import asyncio
import json
import logging
import time
from dataclasses import asdict
from typing import Dict, List

from track_events import TrackEvent, EventType
from event_queue import EventBridge
from llm_request import describe_batch_cheap

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_worker")


class LLMWorker:
    def __init__(self, bridge: EventBridge, event_log_path="events_log.jsonl", description_log_path="descriptions_log.jsonl"):
        self.bridge = bridge
        self.cache: Dict[int, str] = {}
        self.event_log_path = event_log_path
        self.description_log_path = description_log_path

    async def run_forever(self):
        while True:
            batch = await self.bridge.get_batch(max_batch=8, max_wait_s=3.0)
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

        try:
            results = await describe_batch_cheap(needs_description)
        except Exception as exc:
            logger.warning("LLM call failed, skipping this batch: %s", exc)
            if "429" in str(exc):
                await asyncio.sleep(3)
            return

        for event, description in zip(needs_description, results):
            self.cache[event.track_id] = description
            logger.info("track %s (%s): %s", event.track_id, event.event_type.value, description)
            self._log_description(event, description)

    def _log_events(self, batch):
        with open(self.event_log_path, "a") as f:
            for e in batch:
                row = asdict(e)
                row["event_type"] = e.event_type.value
                row.pop("crop", None)   # numpy array isn't JSON-serializable, drop it
                f.write(json.dumps(row) + "\n")

    def _log_description(self, event: TrackEvent, description: str):
        """Log LLM-generated descriptions to a separate file."""
        log_entry = {
            "track_id": event.track_id,
            "event_type": event.event_type.value,
            "class_name": event.class_name,
            "description": description,
            "timestamp": time.time(),
            "frame_idx": event.frame_idx,
            "bbox": event.bbox,
            "confidence": event.confidence
        }
        with open(self.description_log_path, "a") as f:
            f.write(json.dumps(log_entry) + "\n")