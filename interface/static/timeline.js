// Visit timeline: one bar per visit over a sliding time window, from /api/timeline.
(() => {
  "use strict";

  const body = document.getElementById("tl-body");
  const lanesEl = document.getElementById("tl-lanes");
  const emptyEl = document.getElementById("tl-empty");
  const cursor = document.getElementById("tl-cursor");
  const cursorTime = document.getElementById("tl-cursor-time");
  const tip = document.getElementById("tl-tip");
  const axis = document.getElementById("tl-axis");
  const countEl = document.getElementById("tl-count");
  const buttons = [...document.querySelectorAll(".tl-win")];
  if (!body || !lanesEl || !axis || !tip) return;
  const panel = body.closest(".timeline-panel");

  const WINDOWS = [900, 3600, 21600, 86400];
  const LABELS = { 900: "15 minutes", 3600: "hour", 21600: "6 hours", 86400: "24 hours" };
  const MAX_LANES = 5;
  const LANE_H = 15;
  const POLL_MS = 5000;
  const STORE = "precept.timeline.window";

  const trkId = (id) => `TRK-${String(id).padStart(3, "0")}`;
  const hhmm = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const hhmmss = (ms) => new Date(ms).toLocaleTimeString([], { hour12: false });
  const dur = (s) => {
    s = Math.max(0, Math.round(s));
    return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  };

  let windowS = 3600;
  try { const saved = Number(localStorage.getItem(STORE)); if (WINDOWS.includes(saved)) windowS = saved; } catch (error) { /* ignore */ }
  let spans = [];
  let skew = 0;                    // server clock minus browser clock
  let placed = [];                 // spans currently drawn, for the tooltip
  let hovering = false;
  let t0 = 0;

  const serverNow = () => Date.now() + skew;

  const render = () => {
    const now = serverNow();
    const windowMs = windowS * 1000;
    t0 = now - windowMs;
    const gap = windowMs * 0.004;
    const items = spans
      .map((span) => {
        const start = Number(span.start) * 1000;
        const end = span.end == null ? now : Number(span.end) * 1000;
        return { span, start: Math.max(start, t0), end };
      })
      .filter((item) => Number.isFinite(item.start) && item.end > t0 && item.start < now)
      .sort((a, b) => a.start - b.start);

    const laneEnds = [];
    placed = [];
    lanesEl.replaceChildren();
    items.forEach((item, index) => {
      const end = Math.max(item.end, item.start + gap);
      let lane = laneEnds.findIndex((laneEnd) => laneEnd + gap <= item.start);
      if (lane === -1) lane = laneEnds.length < MAX_LANES ? laneEnds.length : MAX_LANES - 1;
      laneEnds[lane] = Math.max(laneEnds[lane] || 0, end);

      const el = document.createElement("div");
      el.className = "tl-span" + (item.span.active ? " is-active" : "");
      el.dataset.kind = item.span.class_name === "person" ? "person" : "other";
      el.dataset.i = String(index);
      el.style.left = `${((item.start - t0) / windowMs) * 100}%`;
      el.style.width = `${Math.max(0.4, ((end - item.start) / windowMs) * 100)}%`;
      el.style.top = `${lane * LANE_H}px`;
      const widthPx = ((end - item.start) / windowMs) * body.clientWidth;
      if (widthPx >= 48) {                       // too-narrow bars stay unlabelled (see tooltip)
        const label = document.createElement("span");
        label.textContent = trkId(item.span.track_id);
        el.append(label);
      }
      lanesEl.append(el);
      placed.push({ ...item, end });
    });

    emptyEl.hidden = placed.length > 0;
    countEl.textContent = placed.length ? `${placed.length} visit${placed.length === 1 ? "" : "s"}` : "";
    body.setAttribute("aria-label", `Visit timeline: ${placed.length} visits in the last ${LABELS[windowS]}`);

    const ticks = [];
    for (let i = 0; i <= 4; i += 1) {
      const label = document.createElement("span");
      label.textContent = i === 4 ? "NOW" : hhmm(t0 + (windowMs * i) / 4);
      ticks.push(label);
    }
    axis.replaceChildren(...ticks);
  };

  // ---------- data ----------
  const load = async () => {
    if (document.hidden) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(`/api/timeline?window_s=${windowS}`, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const data = await response.json();
      spans = Array.isArray(data.spans) ? data.spans : [];
      skew = typeof data.now === "number" ? data.now * 1000 - Date.now() : 0;
      render();
    } catch (error) {
      countEl.textContent = "offline";
    } finally {
      clearTimeout(timer);
    }
  };

  // ---------- window buttons ----------
  const markButtons = () => buttons.forEach((button) => {
    const on = Number(button.dataset.window) === windowS;
    button.classList.toggle("is-active", on);
    button.setAttribute("aria-pressed", String(on));
  });
  buttons.forEach((button) => button.addEventListener("click", () => {
    windowS = Number(button.dataset.window);
    try { localStorage.setItem(STORE, String(windowS)); } catch (error) { /* ignore */ }
    markButtons();
    render();
    load();
  }));

  // ---------- scrubber cursor + tooltip ----------
  const showTip = (item, x) => {
    const { span } = item;
    tip.replaceChildren();
    const head = document.createElement("b");
    head.textContent = `${trkId(span.track_id)} · ${String(span.class_name || "").toUpperCase()}`;
    const times = document.createElement("div");
    const startMs = Number(span.start) * 1000;
    const endMs = span.end == null ? serverNow() : Number(span.end) * 1000;
    times.textContent = `${hhmmss(startMs)} → ${span.end == null ? "now" : hhmmss(endMs)} (${dur((endMs - startMs) / 1000)})`;
    const desc = document.createElement("span");
    desc.className = "tl-tip-desc";
    desc.textContent = span.description || "No description captured";
    tip.append(head, times, desc);
    tip.hidden = false;
    const panelRect = panel.getBoundingClientRect();
    const bodyRect = body.getBoundingClientRect();
    const left = Math.min(Math.max(bodyRect.left - panelRect.left + x + 12, 4), panelRect.width - tip.offsetWidth - 4);
    tip.style.left = `${left}px`;
    tip.style.bottom = `${panelRect.bottom - bodyRect.top + 6}px`;
  };

  body.addEventListener("pointermove", (event) => {
    hovering = true;
    const rect = body.getBoundingClientRect();
    const x = Math.min(Math.max(event.clientX - rect.left, 0), rect.width);
    cursor.hidden = false;
    cursor.style.left = `${x}px`;
    cursor.classList.toggle("is-flip", x > rect.width - 80);
    cursorTime.textContent = hhmmss(t0 + (x / rect.width) * windowS * 1000);
    lanesEl.querySelectorAll(".is-hover").forEach((el) => el.classList.remove("is-hover"));
    const el = event.target.closest?.(".tl-span");
    if (el && placed[Number(el.dataset.i)]) {
      el.classList.add("is-hover");
      showTip(placed[Number(el.dataset.i)], x);
    } else {
      tip.hidden = true;
    }
  });
  body.addEventListener("pointerleave", () => {
    hovering = false;
    cursor.hidden = true;
    tip.hidden = true;
    lanesEl.querySelectorAll(".is-hover").forEach((el) => el.classList.remove("is-hover"));
    render();
  });

  const scheduleTimers = () => {
    clearInterval(loadTimer);
    clearInterval(renderTimer);
    loadTimer = setInterval(load, POLL_MS);
    renderTimer = setInterval(
      () => { if (!document.hidden && !hovering) render(); },
      document.body.classList.contains("perf-mode") ? 5000 : 1000
    );
  };

  let loadTimer;
  let renderTimer;
  document.addEventListener("precept:perfchange", scheduleTimers);
  markButtons();
  render();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });
  scheduleTimers();
  load();
})();