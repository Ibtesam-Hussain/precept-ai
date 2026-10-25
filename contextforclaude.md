# Precept AI — Current Project Context

Last updated: 2026-10-25. This file reflects the current repository snapshot;
use it instead of older scaffold notes that may describe removed behavior.

## Purpose

Precept combines YOLO object tracking, event-based track lifecycle handling,
multimodal LLM descriptions, SQLite persistence, optional speech narration,
and a local browser dashboard.

## Run the application

The repository includes a Python virtual environment at `venv/`. Activate it
before running the app so dependencies such as FastAPI and `pyttsx3` come from
the same environment:

```powershell
.\venv\Scripts\Activate.ps1
python main.py --source 0
```

The default dashboard is at `http://localhost:8000`. `main.py` accepts
`--source`, `--model`, `--visualize`, `--burn-boxes`, and `--dashboard-port`.
The default publishes a clean camera frame for the browser HUD; add
`--burn-boxes` to burn YOLO annotations into the published video. `--visualize`
opens the annotated OpenCV debug window and is not needed for the dashboard.
The app starts the tracker and Uvicorn dashboard on background threads, then
runs the async event ingestor and LLM worker concurrently in the main thread.
Press `q` in the OpenCV window to stop visualization; stop the process to stop
the app.

## Runtime architecture

1. `tracker_pipeline.py` loads YOLO and consumes frames with persistent track
   IDs. By default it publishes the original clean frame as a quality-70 JPEG
   to `frame_bus.py`; `--burn-boxes` selects the annotated frame instead.
   Annotation is skipped when neither burn-boxes nor visualization needs it.
   The tracker also publishes a latest-only snapshot of visible tracks with
   boxes normalized to the published frame, extracts object crops, and sends
   lifecycle events to `EventBridge`.
2. `debounce.py` emits `TRACK_NEW`, `TRACK_STABLE`,
   `TRACK_APPEARANCE_CHANGED`, and `TRACK_EXITED` as track state changes are
   detected across frames.
3. `event_queue.py` transfers individual events from the synchronous tracker
   to the async `EventIngestor`.
4. `tracker_pipeline.py` encodes event crops and updates the in-memory
   `thumbnails.py` cache on the tracker thread, independently of LLM latency.
   It tracks the current in-memory active track IDs; exits clear their thumbs.
5. `EventIngestor` in `llm_worker.py` writes every received event to SQLite.
   Stable/appearance-changed events and their resized JPEG crops are inserted
   atomically as `pending` rows in the durable `description_jobs` table.
   `main.py` runs the ingestor and `LLMWorker` concurrently.
6. `LLMWorker` claims due database jobs as `in_progress` and batches up to 8.
   Successful jobs become `done`; retryable failures return to `pending` with
   a persisted next-attempt time; permanent or exhausted failures become
   `failed`. At startup, interrupted jobs are recovered if attempts remain.
   Slow Groq requests delay descriptions, but not event persistence.
7. `llm_request.py` sends crop data to Groq using `qwen/qwen3.8-27b`, streams
   completion text, caps completion tokens at 256, and splits calls into groups
   of at most 3 images to respect the model limit. The SDK's automatic retries
   are disabled; transient rate-limit, connection, timeout, and server errors
   are scheduled in SQLite with exponential backoff, honoring numeric
   `Retry-After` headers. Jobs get at most 5 attempts; non-retryable errors or
   exhausted jobs are marked `failed`.
8. `storage.py` stores event and description metadata in `precept.db` using
   SQLite WAL mode. The path is relative to the process working directory.
9. `tts_narrator.py` uses `pyttsx3` in a background thread so speech does not
   block tracking or the LLM request loop.

## Dashboard

`interface/dashboard.py` serves `interface/static/` and provides:

- `/api/status` — dashboard health and update timestamp
- `/api/system` — tracker FPS, recent successful LLM latency, description-job
  counts, uptime, and update timestamp
- `/api/tracks` — latest tracker frame sequence, dimensions, timestamp, and
  visible normalized tracks
- `/api/scene` — database-backed active tracks filtered against the tracker's
  current in-memory active IDs, with latest descriptions
- `/api/stats` — today's event totals and hourly activity
- `/api/visits` — recent completed track visits
- `/thumb/{track_id}` — current in-memory JPEG crop, or 404 when unavailable
- `/video` — continuous MJPEG (`multipart/x-mixed-replace; boundary=frame`)

`frame_bus.py` is a lock-protected latest-frame buffer shared by the tracker
and dashboard in this single process. The MJPEG generator checks for new frames
at up to 30 Hz and emits only when the tracker has published a different JPEG.
This removes the old one-snapshot-per-second browser polling, but does not
increase YOLO's inference rate.

The static dashboard uses an `<img>` connected once to `/video`. The browser
draws a canvas HUD from `/api/tracks` (polled every 100 ms): animated boxes,
labels, short motion trails, and a selectable target callout populated from
scene descriptions. The HUD handles the frame's aspect ratio and normalized
coordinates, and lets the user toggle layers and sensor effects; those choices
are saved in local storage. Header health chips read `/api/system` every two
seconds. Scene descriptions refresh every three seconds; statistics and visits
refresh every five seconds. The dashboard reports connection, loading, and
feed-error states, and builds dynamic DOM content with `textContent` rather
than interpolating strings as HTML. Feed overlay CSS explicitly honors
`[hidden]` so hidden loading/error overlays do not cover a successfully loaded
frame.

Thumbnails are in memory, not SQLite or disk. The tracker caches crop JPEGs
when crop-bearing lifecycle events are emitted, independently of the LLM worker,
so provider delays do not delay thumbnail availability. Exited tracks are
removed from both the thumbnail cache and the live active-track set. The
dashboard filters persisted scene rows through that live set to avoid showing
stale tracks whose exit event is waiting in the worker queue. Thumbnail URLs
are served with `Cache-Control: no-store`; the browser shows a "NO CROP"
placeholder on 404. The cache is not persistent across process restarts.

Recent Visits currently stores and displays text metadata/descriptions only;
visit crop snapshots are not saved, so there is no durable visual history.
Descriptions are linked to the exact lifecycle event that generated their
description job, so a delayed LLM response is still shown in the correct
completed visit. Older descriptions from before this event link was added use
timestamp-based matching and may not be attached if they arrived after exit.

## Current caveats

There is no automated test suite checked into this repository snapshot.
Description jobs and their crop JPEGs are persisted in SQLite. Interrupted
`in_progress` jobs return to `pending` on startup unless their attempt limit
has been reached. Permanently failed jobs remain failed; retryable errors are
rescheduled up to the configured attempt limit.

Use the venv interpreter consistently. During troubleshooting,
`E:/Python Installation/python.exe` failed to import `pyttsx3` (and later
FastAPI), while the project venv is the intended runtime. If the browser feed
stutters after switching to MJPEG, check actual tracker FPS: camera throughput,
YOLO model size, input resolution, and inference hardware remain the main
limits.

## Live tracker telemetry and profiling

- `runtime_stats.py` is a lock-protected, in-memory latest-value store. It
  computes tracker FPS from a rolling two-second frame window, keeps up to 20
  successful LLM request durations for the average latency, and stores only the
  latest track snapshot (no snapshot history). Uptime is measured from module
  initialization.
- `tracker_pipeline.py` increments the frame sequence and publishes visible
  tracks after inference. Each box is normalized to the width and height of
  the exact frame selected for `/video`; per-track age is measured from its
  first observed frame. The snapshot contains sequence, frame dimensions,
  Unix timestamp, and track list.
- `llm_request.py` records wall-clock duration only after a successful streamed
  request; failures are not added to latency statistics.
- `GET /api/system` returns FPS, average recent successful LLM latency in
  milliseconds, job counts, uptime, and UTC update time. Job counts are based
  on `description_jobs`: `pending` means `status='pending' AND attempts=0`;
  `retrying` means status is not `done` or `failed` and `attempts>0` (including
  scheduled retries and in-progress jobs); `failed` and `done` use their
  respective terminal statuses. `GET /api/tracks` returns the current
  snapshot, or an empty sequence-zero snapshot before the tracker publishes.
  Both responses set `Cache-Control: no-store`.
- Set `PRECEPT_PROFILE=1` to enable the temporary tracker-stage profiler. It
  warms up for 40 seconds, then reports stage mean/max timings and FPS every
  50 frames, along with capture and published-frame details. With profiling
  off (the default), it does not collect or print stage timings.

## Main files

- `main.py` — process orchestration and CLI
- `tracker_pipeline.py` — YOLO frame loop and frame publication
- `frame_bus.py` — latest encoded frame shared with the web server
- `debounce.py`, `track_events.py`, `event_queue.py` — event state and handoff
- `llm_worker.py`, `llm_request.py` — event ingestion, description queue,
  Groq image batching, and descriptions
- `storage.py` — SQLite reads and writes
- `thumbnails.py`, `tts_narrator.py` — in-memory crops and speech output
- `interface/dashboard.py` — FastAPI routes and MJPEG response
- `interface/static/` — dashboard markup, styles, and browser behavior
- `runtime_stats.py` — in-memory FPS, LLM latency, uptime, and track snapshot
- `requirements.txt` — Python dependencies