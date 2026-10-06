# Project Context: Precept AI Vision + LLM Tracking Prototype

## Objective
This repository implements a lightweight vision-language pipeline for real-time object tracking and semantic description. The system detects objects in a source video, assigns persistent IDs across frames, emits track lifecycle events, and for selected events sends cropped images to a multimodal LLM for natural-language interpretation.

## Current implementation

### 1. Entry and orchestration
`main.py` is the application entry point. It:
- parses CLI arguments for source, model path, and visualization mode
- instantiates an `EventBridge`
- starts the tracking loop in a background thread
- runs the async `LLMWorker` in the main event loop

This creates a producer-consumer architecture where tracking and multimodal reasoning run concurrently.

### 2. Detection and tracking pipeline
`tracker_pipeline.py` is the perception layer. It:
- initializes `YOLO(model_path)`
- iterates over frames via `model.track(..., stream=True, persist=True)`
- extracts `xyxy` boxes and `track_id` values
- resolves class names using `model.names`
- crops each detected object from `result.orig_img`
- pushes per-object events to the event bridge using `TrackStateManager.update(...)`

This gives the app frame-level detection with persistent cross-frame identity for tracked objects.

### 3. Track lifecycle and event semantics
`debounce.py` implements the track state machine. It maintains a map of active tracks keyed by `track_id` and emits event objects from `track_events.py`.

Supported states:
- `TRACK_NEW`
- `TRACK_STABLE`
- `TRACK_APPEARANCE_CHANGED`
- `TRACK_EXITED`

The state manager uses:
- `frames_seen` to determine stability after a threshold
- `last_description_frame` and a cooldown window to limit repeated LLM calls
- perceptual hashing via `imagehash.phash` to detect major appearance changes
- `sweep_exits` to remove tracks after inactivity

This creates a simple event-based abstraction layer between low-level vision and the LLM reasoning component.

### 4. Event schema and batching
`track_events.py` defines the `TrackEvent` dataclass and `EventType` enum. Each event stores:
- `track_id`
- `event_type`
- `class_name`
- `bbox`
- `confidence`
- `frame_idx`
- `crop` (optional image array)
- `timestamp`
- `extra`

`event_queue.py` wraps a Python `queue.Queue` behind an async batching API:
- `put(event)` enqueues a track event
- `get_batch(max_batch, max_wait_s)` collects events in a short wait window

This design reduces API overhead by grouping multiple track events into one LLM call.

### 5. LLM reasoning layer
`llm_worker.py` implements the async worker that consumes queued events and decides which ones merit semantic captioning.

Processing flow:
- reads queued events in batches
- logs raw event payloads to `events_log.jsonl`
- filters events with `EventType.TRACK_STABLE` and `EventType.TRACK_APPEARANCE_CHANGED`
- clears cache entries on `TRACK_EXITED`
- calls `describe_batch_cheap(...)`
- stores LLM outputs in a `cache` keyed by `track_id`
- writes natural-language descriptions to `descriptions_log.jsonl`

This worker acts as the semantic reasoning layer and provides the glue between vision events and LLM interpretation.

### 6. Multimodal request construction
`llm_request.py` sends cropped images to a remote multimodal model through OpenRouter.

Implementation details:
- creates an `OpenAI` client with `base_url="https://openrouter.ai/api/v1"`
- loads API credentials from `.env` using `python-dotenv`
- downscales crops only when needed so the longest side is at most 384 pixels, using `cv2.resize` with `INTER_AREA`
- JPEG-encodes crops at quality 70 using `cv2.IMWRITE_JPEG_QUALITY`
- converts the image bytes to base64
- inserts them into a multimodal OpenAI chat payload as `image_url` entries
- sends a prompt that instructs the model to describe each image in a single sentence, in order
- currently selects `google/gemma-4-31b-it:free` on OpenRouter
- retains `qwen/qwen3.8-27b` as a commented paid-model option; the previous `qwen/qwen3.8-27b:free` slug returned HTTP 404 because that model was no longer available on the free tier

The response is split by newline and mapped back to the batch of events. The function returns a list of description strings aligned to the event list.

### 7. API batching and rate-limit handling
The worker requests batches of up to 8 events and waits up to 3 seconds for events to accumulate (`max_wait_s=3.0`). If an LLM request exception contains `429`, it waits 4 seconds before continuing. Other API errors, including the previous Qwen model HTTP 404, are logged and skipped without that backoff.

The Gemma model selection is present in the current source; its successful runtime behavior has not been confirmed in the captured logs. The observed logs confirm the camera/YOLO stream ran, while OpenRouter rejected the former Qwen free-tier model slug.

## Data flow
The actual runtime path is:

1. `main.py` spawns tracking thread and async LLM loop
2. `tracker_pipeline.py` reads frames and emits track events
3. `TrackStateManager` decides when a track is new/stable/changed/exited
4. `EventBridge` batches events
5. `LLMWorker` selects image-bearing events
6. `describe_batch_cheap(...)` sends cropped frames to the LLM
7. descriptions are cached and logged to JSONL files

## Current system characteristics
This project is an early-stage prototype and is intentionally modular but not yet production-grade.

Present:
- real-time object tracking using YOLO
- track-level event modeling
- asynchronous queue processing
- multimodal image-to-text reasoning via OpenRouter, currently configured for Gemma 4 31B Instruct free tier
- crop payload reduction (maximum longest side 384 px, JPEG quality 70)
- batch wait up to 3 seconds and 4-second backoff for HTTP 429 errors
- JSON-based logging for traceability and experimentation

Missing / not implemented yet:
- persistent database storage
- query layer over historical track states
- web/API frontend
- alerting or rule engine
- robust monitoring, retries, and failure recovery
- security and config management for deployment

## Dependencies
From `requirements.txt`:
- `ultralytics`
- `opencv-python`
- `imagehash`
- `Pillow`
- `openai`
- `python-dotenv`

## Architectural interpretation
This repo is best understood as a vision-language streaming pipeline:
- the detector/tracker handles perception
- the state manager handles temporal event reasoning
- the LLM handles semantic interpretation of boxed object crops

The project is currently a research/POC pipeline rather than a production-ready system, but it cleanly demonstrates the core pattern of combining detection, temporal tracking, and multimodal reasoning.

## Key files
- `main.py` — bootstraps the app
- `tracker_pipeline.py` — video processing and tracking
- `debounce.py` — state machine for track lifecycle transitions
- `track_events.py` — event definitions
- `event_queue.py` — async producer/consumer queue
- `llm_worker.py` — reasoning pipeline and caching
- `llm_request.py` — multimodal LLM API integration
- `README.md` — architecture roadmap and project notes
- `requirements.txt` — dependency list

## Summary
The repository implements a modular prototype for surveillance-style object understanding using YOLO + event tracking + LLM-based visual description. The core novelty is not just detection, but the conversion of tracked object crops into semantic descriptions that can be logged, queried, and extended into higher-level behavior analysis.
