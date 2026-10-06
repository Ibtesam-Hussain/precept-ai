import time
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, Response
import storage
import frame_bus
import thumbnails

app = FastAPI()
storage.init_db()


@app.get("/api/scene")
def scene():
    return {"tracks": storage.get_active_tracks()}


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


def _mjpeg_generator():
    boundary = b"--frame"
    while True:
        frame = frame_bus.get_frame()
        if frame is not None:
            yield (boundary + b"\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(0.05)


@app.get("/video")
def video():
    return StreamingResponse(_mjpeg_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE_HTML


PAGE_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Precept AI — Dashboard</title>
<style>
  body { background:#0d0d0d; color:#eee; font-family: system-ui, sans-serif; margin:0; padding:16px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:0.05em; color:#8ab4f8; margin:0 0 10px; }
  .top { display:grid; grid-template-columns: 1.4fr 1fr; gap:16px; }
  .panel { background:#161616; border-radius:10px; padding:14px; }
  .video-box img { width:100%; border-radius:8px; display:block; background:#000; }

  .scene-item { display:flex; gap:10px; background:#1e1e1e; border-radius:8px; padding:8px; margin-bottom:8px; align-items:center; }
  .scene-item img { width:52px; height:52px; object-fit:cover; border-radius:6px; background:#333; flex-shrink:0; }
  .scene-meta { font-size:12px; color:#888; }
  .scene-text { font-size:13px; }

  .stats-strip { margin-top:16px; display:flex; gap:24px; align-items:flex-end; }
  .stats-numbers { font-size:13px; color:#ccc; white-space:nowrap; }
  .stats-numbers b { color:#fff; }
  .hourly-chart { display:flex; gap:3px; align-items:flex-end; height:40px; flex:1; }
  .hourly-bar { flex:1; background:#4f8cff; border-radius:2px 2px 0 0; min-height:2px; }

  .feed { margin-top:16px; }
  .visit-card { background:#161616; border-radius:10px; padding:12px 14px; margin-bottom:10px; }
  .visit-head { font-size:12px; color:#8ab4f8; margin-bottom:4px; }
  .visit-narrative { font-size:13px; color:#ddd; line-height:1.5; }
  .empty { color:#666; font-size:13px; }
</style>
</head>
<body>

<div class="top">
  <div class="panel video-box">
    <h2>Live Feed</h2>
    <img src="/video">
  </div>
  <div class="panel">
    <h2>Current Scene</h2>
    <div id="scene"><div class="empty">Nothing in frame</div></div>
  </div>
</div>

<div class="panel stats-strip-wrap">
  <h2>Today</h2>
  <div class="stats-strip">
    <div class="stats-numbers" id="stats-numbers">—</div>
    <div class="hourly-chart" id="hourly-chart"></div>
  </div>
</div>

<div class="feed">
  <h2>Activity</h2>
  <div id="visits"><div class="empty">No activity yet</div></div>
</div>

<script>
function timeAgo(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
}

async function refreshScene() {
  const res = await fetch("/api/scene");
  const data = await res.json();
  const el = document.getElementById("scene");
  if (!data.tracks.length) { el.innerHTML = '<div class="empty">Nothing in frame</div>'; return; }
  el.innerHTML = data.tracks.map(t => `
    <div class="scene-item">
      <img src="/thumb/${t.track_id}" onerror="this.style.visibility='hidden'">
      <div>
        <div class="scene-meta">track ${t.track_id} · ${t.class_name}</div>
        <div class="scene-text">${t.description || "..."}</div>
      </div>
    </div>`).join("");
}

async function refreshStats() {
  const res = await fetch("/api/stats");
  const d = await res.json();
  document.getElementById("stats-numbers").innerHTML =
    `<b>${d.total_people}</b> people · <b>${d.total_objects}</b> objects` +
    (d.busiest_hour !== null ? ` · busiest: <b>${d.busiest_hour}:00</b>` : "");

  const max = Math.max(1, ...d.hourly.map(h => h.count));
  const byHour = Object.fromEntries(d.hourly.map(h => [h.hour, h.count]));
  const chart = document.getElementById("hourly-chart");
  chart.innerHTML = Array.from({length: 24}, (_, h) => {
    const c = byHour[h] || 0;
    const pct = Math.max(4, (c / max) * 100);
    return `<div class="hourly-bar" style="height:${pct}%" title="${h}:00 — ${c}"></div>`;
  }).join("");
}

async function refreshVisits() {
  const res = await fetch("/api/visits");
  const data = await res.json();
  const el = document.getElementById("visits");
  if (!data.visits.length) { el.innerHTML = '<div class="empty">No activity yet</div>'; return; }
  el.innerHTML = data.visits.map(v => `
    <div class="visit-card">
      <div class="visit-head">Track ${v.track_id} — ${v.class_name} — ${timeAgo(v.start)}, present ${v.duration}s</div>
      <div class="visit-narrative">${v.narrative.length ? "→ " + v.narrative.join(" → ") : "(no description captured)"}</div>
    </div>`).join("");
}

setInterval(refreshScene, 2000);
setInterval(refreshStats, 10000);
setInterval(refreshVisits, 4000);
refreshScene(); refreshStats(); refreshVisits();
</script>
</body>
</html>
"""