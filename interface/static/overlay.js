// Live detection overlay, drawn in the browser from /api/tracks:
// bracket boxes, labels, trails, click-to-lock with a description callout.
// Also adds status / confidence / dwell time to the target cards rendered by app.js.
(() => {
  "use strict";

  const feedFrame = document.querySelector(".feed-frame");
  const sceneEl = document.getElementById("scene");
  if (!feedFrame || !sceneEl) return;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let reduceMotion = motionQuery.matches;
  const COLOR = { person: "", other: "#ff7a5a", select: "#ffb23e" };
  const TRACK_POLL_MS = 100;
  const SCENE_POLL_MS = 3000;
  const TRAIL_MAX = 28;
  const FADE_MS = 320;
  const STALE_MS = 1500;
  const TYPE_CHARS_PER_S = 55;
  const CALLOUT_W = 270;
  const MONO = 'ui-monospace, "JetBrains Mono", Consolas, monospace';
  const SANS = 'Inter, "Segoe UI", sans-serif';
  const performanceMode = () => document.body.classList.contains("perf-mode");
  const refreshThemeColor = () => {
    COLOR.person = getComputedStyle(document.documentElement).getPropertyValue("--hud").trim();
  };
  motionQuery.addEventListener("change", (event) => { reduceMotion = event.matches; });
  new MutationObserver(refreshThemeColor).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  refreshThemeColor();

  const trkId = (id) => `TRK-${String(id).padStart(3, "0")}`;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const dwell = (s) => {
    const t = Math.max(0, Math.floor(Number(s) || 0));
    const p = (n) => String(n).padStart(2, "0");
    return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
  };

  // ---------- canvas ----------
  const canvas = document.createElement("canvas");
  canvas.className = "overlay-canvas";
  canvas.setAttribute("aria-hidden", "true");
  feedFrame.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  let cw = 0;
  let ch = 0;

  const resize = () => {
    const dpr = window.devicePixelRatio || 1;
    cw = feedFrame.clientWidth;
    ch = feedFrame.clientHeight;
    canvas.width = Math.max(1, Math.round(cw * dpr));
    canvas.height = Math.max(1, Math.round(ch * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  new ResizeObserver(resize).observe(feedFrame);
  resize();

  // ---------- state ----------
  let frameW = 0;
  let frameH = 0;
  let lastSeq = -1;
  let lastSeqAt = 0;
  const view = new Map();          // track_id -> drawable state
  let sceneInfo = new Map();       // track_id -> {event_type, description}
  let selectedId = null;
  let typing = { id: null, text: "", start: 0 };
  let hoverId = null;

  const flags = () => {
    const c = document.body.classList;
    return {
      persons: !c.contains("hide-persons"),
      objects: !c.contains("hide-objects"),
      trails: !c.contains("hide-trails"),
      labels: !c.contains("hide-labels"),
      callout: !c.contains("hide-callout"),
      fill: c.contains("fill-frame"),
    };
  };

  const applySnapshot = (data) => {
    if (!data || typeof data.seq !== "number") return;
    const now = performance.now();
    if (data.seq < lastSeq) view.clear();               // tracker restarted
    const advanced = data.seq !== lastSeq;
    if (advanced) lastSeqAt = now;
    lastSeq = data.seq;
    frameW = Number(data.frame?.w) || 0;
    frameH = Number(data.frame?.h) || 0;

    const seen = new Set();
    for (const t of data.tracks || []) {
      if (!Array.isArray(t.box) || t.box.length !== 4) continue;
      const id = t.track_id;
      seen.add(id);
      let v = view.get(id);
      if (!v) {
        v = { id, box: t.box.slice(), target: t.box.slice(), born: now, gone: 0, trail: [] };
        view.set(id, v);
      }
      v.target = t.box.slice();
      v.cls = String(t.class_name || "object");
      v.conf = Number(t.conf) || 0;
      v.age = Number(t.age_s) || 0;
      v.gone = 0;
      if (advanced) {
        const cx = (t.box[0] + t.box[2]) / 2;
        const cy = (t.box[1] + t.box[3]) / 2;
        const last = v.trail[v.trail.length - 1];
        if (!last || Math.hypot(cx - last[0], cy - last[1]) > 0.003) {
          v.trail.push([cx, cy]);
          if (v.trail.length > TRAIL_MAX) v.trail.shift();
        }
      }
    }
    for (const v of view.values()) if (!seen.has(v.id) && !v.gone) v.gone = now;
  };

  // ---------- polling ----------
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const fetchJson = async (url, timeoutMs) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  };

  const trackLoop = async () => {
    for (;;) {
      const t0 = performance.now();
      if (!document.hidden) {
        try { applySnapshot(await fetchJson("/api/tracks", 2000)); } catch (error) { /* stale handling in draw */ }
      }
      const interval = performanceMode() ? TRACK_POLL_MS * 2 : TRACK_POLL_MS;
      await sleep(Math.max(40, interval - (performance.now() - t0)));
    }
  };

  const sceneLoop = async () => {
    for (;;) {
      if (!document.hidden) {
        try {
          const data = await fetchJson("/api/scene", 3000);
          sceneInfo = new Map((data.tracks || []).map((t) => [t.track_id, t]));
          updateCards();
        } catch (error) { /* keep previous info */ }
      }
      await sleep(SCENE_POLL_MS);
    }
  };

  // ---------- geometry ----------
  const layout = (fill) => {
    if (!frameW || !frameH || !cw || !ch) return null;
    const scale = fill ? Math.max(cw / frameW, ch / frameH) : Math.min(cw / frameW, ch / frameH);
    return { scale, ox: (cw - frameW * scale) / 2, oy: (ch - frameH * scale) / 2 };
  };
  const toPx = (L, box) => {
    const x1 = L.ox + box[0] * frameW * L.scale;
    const y1 = L.oy + box[1] * frameH * L.scale;
    const x2 = L.ox + box[2] * frameW * L.scale;
    const y2 = L.oy + box[3] * frameH * L.scale;
    return { x1, y1, x2, y2, w: x2 - x1, h: y2 - y1 };
  };
  const isVisible = (v, f) => (v.cls === "person" ? f.persons : f.objects);
  const colorOf = (v) => (v.id === selectedId ? COLOR.select : v.cls === "person" ? COLOR.person : COLOR.other);

  // ---------- drawing ----------
  const corners = (p, len) => {
    ctx.beginPath();
    ctx.moveTo(p.x1, p.y1 + len); ctx.lineTo(p.x1, p.y1); ctx.lineTo(p.x1 + len, p.y1);
    ctx.moveTo(p.x2 - len, p.y1); ctx.lineTo(p.x2, p.y1); ctx.lineTo(p.x2, p.y1 + len);
    ctx.moveTo(p.x2, p.y2 - len); ctx.lineTo(p.x2, p.y2); ctx.lineTo(p.x2 - len, p.y2);
    ctx.moveTo(p.x1 + len, p.y2); ctx.lineTo(p.x1, p.y2); ctx.lineTo(p.x1, p.y2 - len);
    ctx.stroke();
  };

  const roundRect = (x, y, w, h, r) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };

  const drawLabel = (v, p, color) => {
    const text = `${trkId(v.id)} ${v.cls.toUpperCase()} ${v.conf.toFixed(2)}`;
    ctx.font = `700 11px ${MONO}`;
    const w = ctx.measureText(text).width + 14;
    const h = 18;
    const x = clamp(p.x1 - 1, 2, Math.max(2, cw - w - 2));
    const y = p.y1 - h - 3 < 2 ? p.y1 + 3 : p.y1 - h - 3;
    ctx.fillStyle = color;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "#04121b";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x + 7, y + h / 2 + 0.5);
  };

  const drawTrail = (v, L, color, alpha) => {
    if (v.trail.length < 2) return;
    for (let i = 1; i < v.trail.length; i += 1) {
      const a = v.trail[i - 1];
      const b = v.trail[i];
      ctx.strokeStyle = color;
      ctx.globalAlpha = alpha * (i / v.trail.length) * 0.75;
      ctx.lineWidth = 1 + (i / v.trail.length) * 2;
      ctx.beginPath();
      ctx.moveTo(L.ox + a[0] * frameW * L.scale, L.oy + a[1] * frameH * L.scale);
      ctx.lineTo(L.ox + b[0] * frameW * L.scale, L.oy + b[1] * frameH * L.scale);
      ctx.stroke();
    }
  };

  const wrap = (text, maxWidth, maxLines) => {
    const words = text.split(/\s+/);
    const lines = [];
    let line = "";
    for (const word of words) {
      const test = line ? `${line} ${word}` : word;
      if (ctx.measureText(test).width > maxWidth && line) { lines.push(line); line = word; } else { line = test; }
    }
    if (line) lines.push(line);
    return lines.slice(0, maxLines);
  };

  const drawCallout = (v, p, now) => {
    const info = sceneInfo.get(v.id);
    const full = (info && info.description) || "Awaiting description…";
    if (typing.id !== v.id || typing.text !== full) typing = { id: v.id, text: full, start: now };
    const chars = reduceMotion ? full.length : Math.floor(((now - typing.start) / 1000) * TYPE_CHARS_PER_S);
    const shown = full.slice(0, chars);
    const typingDone = chars >= full.length;

    ctx.font = `12px ${SANS}`;
    const lines = wrap(shown || " ", CALLOUT_W - 24, 5);
    const lineH = 17;
    const boxH = 14 + 16 + lines.length * lineH + 8;
    const rightSpace = cw - p.x2;
    const placeRight = rightSpace >= CALLOUT_W + 50 || rightSpace >= p.x1;
    const bx = placeRight ? clamp(p.x2 + 46, 8, cw - CALLOUT_W - 8) : clamp(p.x1 - 46 - CALLOUT_W, 8, cw - CALLOUT_W - 8);
    const by = clamp(p.y1 - 6, 64, Math.max(64, ch - boxH - 8));

    // leader line
    const ax = placeRight ? p.x2 : p.x1;
    const ay = p.y1 + Math.min(24, p.h / 3);
    ctx.strokeStyle = COLOR.select;
    ctx.globalAlpha = 0.9;
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(placeRight ? bx - 18 : bx + CALLOUT_W + 18, by + 20);
    ctx.lineTo(placeRight ? bx : bx + CALLOUT_W, by + 20);
    ctx.stroke();
    ctx.fillStyle = COLOR.select;
    ctx.beginPath(); ctx.arc(ax, ay, 3, 0, Math.PI * 2); ctx.fill();

    // panel
    ctx.globalAlpha = 1;
    roundRect(bx, by, CALLOUT_W, boxH, 8);
    ctx.fillStyle = "rgba(4, 12, 20, 0.90)";
    ctx.fill();
    ctx.strokeStyle = "rgba(255, 178, 62, 0.85)";
    ctx.lineWidth = 1;
    ctx.stroke();

    ctx.textBaseline = "top";
    ctx.fillStyle = COLOR.select;
    ctx.font = `700 10px ${MONO}`;
    ctx.fillText(`${trkId(v.id)} · LLM DESCRIPTION`, bx + 12, by + 10);
    ctx.fillStyle = "#cfe4f2";
    ctx.font = `12px ${SANS}`;
    lines.forEach((line, i) => ctx.fillText(line, bx + 12, by + 30 + i * lineH));
    if (!reduceMotion && (!typingDone || Math.floor(now / 500) % 2 === 0)) {
      const last = lines[lines.length - 1] || "";
      const cx = bx + 12 + ctx.measureText(last).width + 2;
      const cy = by + 30 + (lines.length - 1) * lineH;
      ctx.fillStyle = COLOR.select;
      ctx.fillRect(cx, cy + 1, 6, 12);
    }
  };

  let lastFrameAt = performance.now();
  let lastDrawAt = 0;
  const draw = (now) => {
    requestAnimationFrame(draw);
    const minFrameMs = performanceMode() || reduceMotion ? 1000 / 15 : 0;
    if (now - lastDrawAt < minFrameMs) return;
    lastDrawAt = now;
    const dt = Math.min(0.1, (now - lastFrameAt) / 1000);
    lastFrameAt = now;
    ctx.clearRect(0, 0, cw, ch);

    const f = flags();
    const L = layout(f.fill);
    const stale = now - lastSeqAt > STALE_MS;
    if (!L || stale || document.hidden) return;

    const ease = reduceMotion ? 1 : 1 - Math.pow(0.0005, dt);   // ~smooth follow at any frame rate
    const items = [];
    for (const v of view.values()) {
      for (let i = 0; i < 4; i += 1) v.box[i] += (v.target[i] - v.box[i]) * ease;
      let alpha = 1;
      if (v.gone) {
        alpha = reduceMotion ? 0 : 1 - (now - v.gone) / FADE_MS;
        if (alpha <= 0) { view.delete(v.id); if (selectedId === v.id) select(null); continue; }
      }
      if (!isVisible(v, f)) continue;
      items.push({ v, alpha });
    }

    // trails under boxes
    if (f.trails) items.forEach(({ v, alpha }) => drawTrail(v, L, colorOf(v), alpha));

    items.forEach(({ v, alpha }) => {
      const p = toPx(L, v.box);
      const color = colorOf(v);
      const t = reduceMotion ? 1 : clamp((now - v.born) / 260, 0, 1);
      const grow = 1 + (1 - t) * (1 - t) * 0.14;              // lock-on: shrinks into place
      const cx = (p.x1 + p.x2) / 2;
      const cy = (p.y1 + p.y2) / 2;
      const q = { x1: cx - (p.w / 2) * grow, x2: cx + (p.w / 2) * grow, y1: cy - (p.h / 2) * grow, y2: cy + (p.h / 2) * grow };
      q.w = q.x2 - q.x1; q.h = q.y2 - q.y1;

      ctx.globalAlpha = alpha * (0.35 + 0.65 * t);
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = performanceMode() ? 0 : v.id === selectedId || v.id === hoverId ? 16 : 9;
      ctx.lineWidth = v.id === selectedId ? 2.5 : 2;
      corners(q, clamp(Math.min(q.w, q.h) * 0.2, 10, 30));
      ctx.shadowBlur = 0;
      ctx.lineWidth = 1;
      ctx.globalAlpha = alpha * 0.28;
      ctx.strokeRect(q.x1, q.y1, q.w, q.h);

      if (f.labels) { ctx.globalAlpha = alpha; drawLabel(v, q, color); }

      if (v.id === selectedId) {
        ctx.globalAlpha = alpha * 0.85;
        ctx.strokeStyle = COLOR.select;
        ctx.lineWidth = 1.2;
        ctx.setLineDash([6, 6]);
        ctx.lineDashOffset = reduceMotion ? 0 : -now / 60;
        ctx.beginPath();
        ctx.ellipse(cx, cy, (q.w / 2) * 1.28 + 6, (q.h / 2) * 1.18 + 6, 0, 0, Math.PI * 2);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    });

    const sel = items.find(({ v }) => v.id === selectedId);
    if (sel && f.callout) {
      ctx.globalAlpha = 1;
      drawCallout(sel.v, toPx(L, sel.v.box), now);
    }
    ctx.globalAlpha = 1;
    updateCardsThrottled(now);
  };

  // ---------- selection + hit testing ----------
  const select = (id, { scroll = false } = {}) => {
    selectedId = id === selectedId ? null : id;
    typing = { id: null, text: "", start: 0 };
    sceneEl.querySelectorAll(".scene-item").forEach((item) => {
      const on = selectedId !== null && Number(item.dataset.trackId) === selectedId;
      item.classList.toggle("is-selected", on);
      item.setAttribute("aria-pressed", String(on));
      if (on && scroll) item.scrollIntoView({ block: "nearest" });
    });
  };

  const hit = (event) => {
    const f = flags();
    const L = layout(f.fill);
    if (!L) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    let best = null;
    let bestArea = Infinity;
    for (const v of view.values()) {
      if (v.gone || !isVisible(v, f)) continue;
      const p = toPx(L, v.box);
      if (x >= p.x1 && x <= p.x2 && y >= p.y1 && y <= p.y2 && p.w * p.h < bestArea) { best = v.id; bestArea = p.w * p.h; }
    }
    return best;
  };
  canvas.addEventListener("click", (event) => {
    const id = hit(event);
    if (id === null) select(null); else if (id === selectedId) select(null); else { selectedId = null; select(id, { scroll: true }); }
  });
  canvas.addEventListener("pointermove", (event) => {
    hoverId = hit(event);
    canvas.style.cursor = hoverId === null ? "default" : "pointer";
  });
  canvas.addEventListener("pointerleave", () => { hoverId = null; });

  // ---------- target cards (rendered by app.js; we enrich them) ----------
  const STATUS = { track_new: ["NEW", "new"], track_stable: ["STABLE", "stable"], track_appearance_changed: ["CHANGED", "changed"] };

  const decorateCards = () => {
    sceneEl.querySelectorAll(".scene-item").forEach((item) => {
      if (item.dataset.decorated) return;
      const match = /TRK-(\d+)/.exec(item.querySelector(".scene-id")?.textContent || "");
      if (!match) return;
      item.dataset.trackId = String(Number(match[1]));
      item.dataset.decorated = "1";
      item.tabIndex = 0;
      item.setAttribute("role", "button");
      item.setAttribute("aria-label", `Select track ${Number(match[1])}`);
      item.setAttribute("aria-pressed", String(Number(match[1]) === selectedId));
      item.classList.toggle("is-selected", Number(match[1]) === selectedId);
      const chip = document.createElement("span");
      chip.className = "status-chip";
      chip.hidden = true;
      item.querySelector(".scene-header")?.appendChild(chip);
      const meta = document.createElement("div");
      meta.className = "scene-meta";
      meta.append(document.createElement("span"), document.createElement("span"));
      item.querySelector(".scene-details")?.appendChild(meta);
    });
    updateCards();
  };

  const updateCards = () => {
    sceneEl.querySelectorAll(".scene-item[data-decorated]").forEach((item) => {
      const id = Number(item.dataset.trackId);
      const v = view.get(id);
      const info = sceneInfo.get(id);
      const chip = item.querySelector(".status-chip");
      const status = info && STATUS[info.event_type];
      if (chip) {
        chip.hidden = !status;
        if (status) { chip.textContent = status[0]; chip.dataset.kind = status[1]; }
      }
      const spans = item.querySelectorAll(".scene-meta span");
      if (spans.length === 2) {
        spans[0].textContent = v ? `CONF ${v.conf.toFixed(2)}` : "CONF --";
        spans[1].textContent = v ? `DWELL ${dwell(v.age)}` : "DWELL --";
      }
    });
  };
  let lastCardUpdate = 0;
  const updateCardsThrottled = (now) => {
    if (now - lastCardUpdate < 500) return;
    lastCardUpdate = now;
    updateCards();
  };

  sceneEl.addEventListener("click", (event) => {
    const item = event.target.closest(".scene-item[data-track-id]");
    if (item) select(Number(item.dataset.trackId));
  });
  sceneEl.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const item = event.target.closest(".scene-item[data-track-id]");
    if (!item) return;
    event.preventDefault();
    select(Number(item.dataset.trackId));
  });
  // app.js rebuilds the list every few seconds: re-decorate when direct children change
  new MutationObserver(decorateCards).observe(sceneEl, { childList: true });
  decorateCards();

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && selectedId !== null) select(null);
  });
  document.addEventListener("precept:perfchange", () => { lastDrawAt = 0; });

  trackLoop();
  sceneLoop();
  requestAnimationFrame(draw);
})();