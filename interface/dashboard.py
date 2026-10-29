import asyncio
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import frame_bus
import runtime_stats
import storage
import thumbnails

app = FastAPI()
storage.init_db()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/status")
def status():
    return {
        "status": "ok",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/system")
def system():
    latency_ms = runtime_stats.get_llm_latency_ms()
    return JSONResponse(
        content={
            "fps": runtime_stats.get_fps(),
            "llm_latency_ms": None if latency_ms is None else round(latency_ms),
            "jobs": storage.get_job_counts(),
            "uptime_s": round(time.monotonic() - runtime_stats.started_at),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/tracks")
def tracks():
    return JSONResponse(
        content=runtime_stats.get_tracks_snapshot(),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/events")
def events(limit: int = 30, after_id: Optional[str] = None):
    try:
        parsed_after_id = int(after_id) if after_id is not None else None
        if parsed_after_id is not None and not (
            -(2 ** 63) <= parsed_after_id < 2 ** 63
        ):
            parsed_after_id = None
    except (ValueError, OverflowError):
        parsed_after_id = None
    return JSONResponse(
        content=storage.get_events(
            limit=limit,
            after_id=parsed_after_id,
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/timeline")
def timeline(window_s: int = 3600):
    return JSONResponse(
        content=storage.get_timeline(
            window_s=window_s,
            live_track_ids=thumbnails.get_active_track_ids(),
        ),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/scene")
def scene():
    active_track_ids = thumbnails.get_active_track_ids()
    tracks = [
        track for track in storage.get_active_tracks()
        if track["track_id"] in active_track_ids
    ]
    return {"tracks": tracks}


@app.get("/api/stats")
def stats():
    return storage.get_today_stats()


@app.get("/api/visits")
def visits():
    return {"visits": storage.get_track_visits(20)}


@app.get("/thumb/{track_id}")
def thumb(track_id: int):
    data = thumbnails.get_thumb(track_id)
    if data is None:
        return Response(status_code=404, headers={"Cache-Control": "no-store"})
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


async def stream_video():
    last_frame = None
    while True:
        frame = frame_bus.get_frame()
        if frame is not None and frame is not last_frame:
            last_frame = frame
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                + frame + b"\r\n"
            )
        await asyncio.sleep(1 / 30)


@app.get("/video")
def video():
    return StreamingResponse(
        stream_video(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache", "Pragma": "no-cache"},
    )


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")