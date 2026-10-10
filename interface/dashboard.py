import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import frame_bus
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
        return Response(status_code=404)
    return Response(content=data, media_type="image/jpeg")


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