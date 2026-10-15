import asyncio
import logging
from typing import List, Optional

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from event_queue import EventBridge
from llm_request import describe_batch_cheap
import storage
import tts_narrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_worker")


def _get_retry_after_seconds(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _is_retryable_provider_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError),
    ):
        return True
    return isinstance(exc, APIStatusError) and (
        exc.status_code in (408, 425, 429) or exc.status_code >= 500
    )


class EventIngestor:  #Event Ingestion Storage
    def __init__(self, bridge: EventBridge):
        self.bridge = bridge
        storage.init_db()
        storage.recover_interrupted_description_jobs()

    async def run_forever(self):
        while True:
            event = await self.bridge.get_event()
            await asyncio.to_thread(storage.persist_event, event)


class LLMWorker:
    def __init__(self):
        tts_narrator.start()

    async def run_forever(self):
        while True:
            jobs = await asyncio.to_thread(storage.claim_description_jobs, 8)
            if not jobs:
                await asyncio.sleep(0.5)
                continue
            await self._process_batch(jobs)

    async def _process_batch(self, jobs: List[storage.DescriptionJob]):
        events = [job.event for job in jobs]
        try:
            results = await describe_batch_cheap(events)
        except Exception as exc:
            retrying, failed = storage.fail_description_jobs(
                [job.job_id for job in jobs],
                str(exc),
                retryable=_is_retryable_provider_error(exc),
                retry_after_seconds=_get_retry_after_seconds(exc),
            )
            logger.warning(
                "LLM call failed; %s job(s) scheduled for retry, "
                "%s job(s) marked failed: %s",
                retrying,
                failed,
                exc,
            )
            return

        for job, description in zip(jobs, results):
            event = job.event
            logger.info("track %s (%s): %s", event.track_id, event.event_type.value, description)

            storage.complete_description_job(job.job_id, event, description)
            tts_narrator.speak(description)