# Precept AI — Current Project Context

Last updated: 2026-10-10. This file reflects the current repository snapshot;
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
python main.py --source 0 --visualize
```

The default dashboard is at `http://localhost:8000`. `main.py` accepts
`--source`, `--model`, `--visualize`, and `--dashboard-port`. It starts the
tracker and Uvicorn dashboard on background threads, then runs the async LLM
worker in the main thread. Press `q` in the OpenCV window to stop visualization;
stop the process to shut down the app.

## Runtime architecture

1. `tracker_pipeline.py` loads YOLO and consumes frames with persistent track
   IDs. It publishes annotated quality-70 JPEGs to `frame_bus.py`, extracts
   object crops, and sends lifecycle events to `EventBridge`.
2. `debounce.py` emits `TRACK_NEW`, `TRACK_STABLE`,
   `TRACK_APPEARANCE_CHANGED`, and `TRACK_EXITED` as track state changes are
   detected across frames.
3. `event_queue.py` transfers events from the synchronous tracker to the async
   worker and batches up to 8 events with a 3-second collection wait.
4. `tracker_pipeline.py` encodes event crops and updates the in-memory
   `thumbnails.py` cache on the tracker thread, independently of LLM latency.
   It tracks the current in-memory active track IDs; exits clear their thumbs.
5. `llm_worker.py` persists events, requests descriptions only for stable or
   appearance-changed events, then stores successful descriptions and queues
   narration. It processes batches serially, so provider retries can delay later
   event persistence and descriptions, but do not block tracking/video.
6. `llm_request.py` sends crop data to Groq using `qwen/qwen3.8-27b`, streams
   completion text, caps completion tokens at 256, and splits calls into groups
   of at most 3 images to respect the model limit. Groq rate-limit retries may
   still delay processing; a batch that ultimately fails is skipped.
7. `storage.py` stores event and description metadata in `precept.db` using
   SQLite WAL mode. The path is relative to the process working directory.
8. `tts_narrator.py` uses `pyttsx3` in a background thread so speech does not
   block tracking or the LLM request loop.

## Dashboard

`interface/dashboard.py` serves `interface/static/` and provides:

- `/api/status` — dashboard health and update timestamp
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

The static dashboard uses an `<img>` connected once to `/video`. JavaScript
refreshes scene, statistics, and visits every five seconds and reports
connection, loading, and feed-error states. It builds DOM content with
`textContent` instead of interpolating dynamic strings as HTML. Feed overlay
CSS explicitly honors `[hidden]` so hidden loading/error overlays do not cover
a successfully loaded frame.

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

## Current caveats

There is no automated test suite checked into this repository snapshot.

Use the venv interpreter consistently. During troubleshooting,
`E:/Python Installation/python.exe` failed to import `pyttsx3` (and later
FastAPI), while the project venv is the intended runtime. If the browser feed
stutters after switching to MJPEG, check actual tracker FPS: camera throughput,
YOLO model size, input resolution, and inference hardware remain the main
limits.

## Main files

- `main.py` — process orchestration and CLI
- `tracker_pipeline.py` — YOLO frame loop and frame publication
- `frame_bus.py` — latest encoded frame shared with the web server
- `debounce.py`, `track_events.py`, `event_queue.py` — event state and handoff
- `llm_worker.py`, `llm_request.py` — event processing, Groq image batching,
  descriptions
- `storage.py` — SQLite reads and writes
- `thumbnails.py`, `tts_narrator.py` — in-memory crops and speech output
- `interface/dashboard.py` — FastAPI routes and MJPEG response
- `interface/static/` — dashboard markup, styles, and browser behavior
- `requirements.txt` — Python dependencies