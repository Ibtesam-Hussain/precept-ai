from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import storage

app = FastAPI()
storage.init_db()


@app.get("/api/recent")
def recent():
    return {
        "descriptions": storage.get_recent_descriptions(30),
        "events": storage.get_recent_events(50),
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Precept AI — Live Feed</title>
<style>
  body { background:#111; color:#eee; font-family: system-ui, sans-serif; margin:0; padding:20px; }
  h1 { font-size:18px; color:#9ad; }
  .cols { display:flex; gap:20px; }
  .col { flex:1; }
  .item { background:#1c1c1c; border-radius:6px; padding:10px 14px; margin-bottom:8px; }
  .item .meta { color:#888; font-size:12px; margin-bottom:4px; }
  .item .text { font-size:14px; }
</style>
</head>
<body>
  <h1>Descriptions</h1>
  <div id="descriptions"></div>
  <h1 style="margin-top:30px;">Raw Events</h1>
  <div id="events"></div>

<script>
async function refresh() {
  const res = await fetch("/api/recent");
  const data = await res.json();

  const d = document.getElementById("descriptions");
  d.innerHTML = data.descriptions.map(x => `
    <div class="item">
      <div class="meta">track ${x.track_id} · ${x.class_name} · ${x.event_type}</div>
      <div class="text">${x.description}</div>
    </div>`).join("");

  const e = document.getElementById("events");
  e.innerHTML = data.events.slice(0, 15).map(x => `
    <div class="item">
      <div class="meta">track ${x.track_id} · ${x.class_name} · frame ${x.frame_idx}</div>
      <div class="text">${x.event_type}</div>
    </div>`).join("");
}
setInterval(refresh, 2000);
refresh();
</script>
</body>
</html>
"""