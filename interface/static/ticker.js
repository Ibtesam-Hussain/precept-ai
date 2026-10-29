// Activity log: live event stream (from /api/events) alongside the existing Visits list.
(() => {
  "use strict";

  const feed = document.getElementById("events-feed");
  const visits = document.getElementById("visits");
  const tabEvents = document.getElementById("tab-events");
  const tabVisits = document.getElementById("tab-visits");
  const tag = document.getElementById("log-tag");
  if (!feed || !visits || !tabEvents || !tabVisits || !tag) return;

  const STORE = "precept.log.tab";
  const POLL_MS = 1500;
  const RECONCILE_EVERY = 3;       // every 3rd poll, re-read the latest rows to pick up late descriptions
  const INITIAL = 30;
  const MAX_ROWS = 60;
  const STATUS = {
    track_new: ["NEW", "new"],
    track_stable: ["STB", "stable"],
    track_appearance_changed: ["CHG", "changed"],
    track_exited: ["EXIT", "exit"],
  };

  const trkId = (id) => `TRK-${String(id).padStart(3, "0")}`;
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });

  const rows = new Map();          // event id -> { el, desc }
  let lastId = 0;
  let started = false;
  let cycle = 0;
  let offline = false;
  let currentTab = "events";

  // ---------- tabs ----------
  const updateTag = () => {
    tag.textContent = currentTab === "events" ? (offline ? "Offline" : "Live") : "Latest 20";
  };
  const setTab = (name, persist = true) => {
    currentTab = name === "visits" ? "visits" : "events";
    const events = currentTab === "events";
    feed.hidden = !events;
    visits.hidden = events;
    [[tabEvents, events], [tabVisits, !events]].forEach(([button, on]) => {
      button.classList.toggle("is-active", on);
      button.setAttribute("aria-selected", String(on));
      button.tabIndex = on ? 0 : -1;
    });
    updateTag();
    if (persist) { try { localStorage.setItem(STORE, currentTab); } catch (error) { /* ignore */ } }
  };
  tabEvents.addEventListener("click", () => setTab("events"));
  tabVisits.addEventListener("click", () => setTab("visits"));
  [tabEvents, tabVisits].forEach((button) => button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const next = event.key === "Home" ? "events"
      : event.key === "End" ? "visits"
        : currentTab === "events" ? "visits" : "events";
    event.preventDefault();
    setTab(next);
    (next === "events" ? tabEvents : tabVisits).focus();
  }));

  // ---------- rows ----------
  const placeholder = (text, loading = false) => {
    const div = document.createElement("div");
    div.className = loading ? "empty loading" : "empty";
    div.textContent = text;
    return div;
  };

  const buildRow = (event, animate) => {
    const row = document.createElement("article");
    row.className = "ev-row";
    row.dataset.id = String(event.id);
    if (!animate) row.style.animation = "none";

    const time = document.createElement("span");
    time.className = "ev-time";
    time.textContent = typeof event.timestamp === "number" ? fmtTime(event.timestamp) : "--:--:--";

    const kind = String(event.event_type || "").toLowerCase();
    const chip = document.createElement("span");
    chip.className = "status-chip";
    const status = STATUS[kind];
    chip.textContent = status ? status[0] : kind.replace("track_", "").slice(0, 4).toUpperCase() || "EVT";
    chip.dataset.kind = status ? status[1] : "";

    const main = document.createElement("div");
    const line = document.createElement("div");
    const id = document.createElement("span");
    id.className = "ev-id";
    id.textContent = trkId(event.track_id);
    const cls = document.createElement("span");
    cls.className = "ev-class";
    cls.textContent = String(event.class_name || "");
    line.append(id, cls);
    if (typeof event.confidence === "number" && event.confidence > 0 && kind !== "track_exited") {   // exit events carry 0.0
      const conf = document.createElement("span");
      conf.className = "ev-conf";
      conf.textContent = event.confidence.toFixed(2);
      line.append(conf);
    }
    const desc = document.createElement("p");
    desc.className = "ev-desc";
    desc.textContent = event.description || "";
    desc.hidden = !event.description;
    main.append(line, desc);

    row.append(time, chip, main);
    rows.set(event.id, { desc });
    return row;
  };

  const addEvents = (list, animate) => {
    const fresh = (list || []).filter((event) => typeof event.id === "number" && !rows.has(event.id));
    if (!fresh.length) return;
    feed.querySelector(".empty")?.remove();
    fresh.sort((a, b) => a.id - b.id).forEach((event) => {
      feed.prepend(buildRow(event, animate));      // newest ends up on top
      lastId = Math.max(lastId, event.id);
      if (animate) {
        document.dispatchEvent(new CustomEvent("precept:event", { detail: event }));
      }
    });
    while (feed.children.length > MAX_ROWS) {
      const last = feed.lastElementChild;
      rows.delete(Number(last.dataset.id));
      last.remove();
    }
  };

  const reconcile = (list) => {
    (list || []).forEach((event) => {
      const row = rows.get(event.id);
      if (!row || !event.description || row.desc.textContent === event.description) return;
      row.desc.textContent = event.description;
      row.desc.hidden = false;
    });
  };

  // ---------- polling ----------
  const getJson = async (url) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`status ${response.status}`);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  };

  const reset = () => { feed.replaceChildren(); rows.clear(); lastId = 0; started = false; };

  const poll = async () => {
    if (document.hidden) return;
    try {
      if (!started) {
        const data = await getJson(`/api/events?limit=${INITIAL}`);
        reset();
        addEvents(data.events, false);
        if (!rows.size) feed.append(placeholder("No events yet"));
        started = true;
      } else {
        const data = await getJson(`/api/events?after_id=${lastId}&limit=100`);
        if (typeof data.last_id === "number" && data.last_id < lastId) { reset(); return; }   // database was reset
        addEvents(data.events, true);
        cycle += 1;
        if (cycle % RECONCILE_EVERY === 0) reconcile((await getJson(`/api/events?limit=${INITIAL}`)).events);
      }
      if (offline) { offline = false; updateTag(); }
    } catch (error) {
      if (!offline) { offline = true; updateTag(); }
      if (!started && !feed.querySelector(".ev-row")) { feed.replaceChildren(placeholder("Events unavailable")); }
    }
  };

  let saved = null;
  try { saved = localStorage.getItem(STORE); } catch (error) { /* ignore */ }
  feed.replaceChildren(placeholder("Loading events…", true));
  setTab(saved === "visits" ? "visits" : "events", false);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  setInterval(poll, POLL_MS);
  poll();
})();