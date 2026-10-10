const pad = (n) => String(n).padStart(2, "0");
const trkId = (id) => `TRK-${String(id).padStart(3, "0")}`;
const timeOf = (ts) => new Date(ts * 1000).toLocaleTimeString([], {
  hour: "2-digit",
  minute: "2-digit",
});

const elements = {
  clock: document.getElementById("clock"),
  connectionStatus: document.getElementById("connection-status"),
  connectionLabel: document.getElementById("connection-label"),
  liveFeed: document.getElementById("live-feed"),
  feedLoading: document.getElementById("feed-loading"),
  feedError: document.getElementById("feed-error"),
  scene: document.getElementById("scene"),
  targetCount: document.getElementById("target-count"),
  stats: document.getElementById("stats-numbers"),
  hourlyChart: document.getElementById("hourly-chart"),
  visits: document.getElementById("visits"),
};
let feedAvailable = false;

const setStatus = (state, label) => {
  elements.connectionStatus.className = `connection-status ${state}`;
  elements.connectionLabel.textContent = label;
};

const setLoading = (element, message) => {
  element.replaceChildren();
  const loading = document.createElement("div");
  loading.className = "empty loading";
  loading.textContent = message;
  element.appendChild(loading);
};

const setError = (element, message, retry) => {
  element.replaceChildren();
  const wrapper = document.createElement("div");
  wrapper.className = "empty error";
  const text = document.createElement("span");
  text.textContent = message;
  wrapper.appendChild(text);

  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "retry-button";
    button.textContent = "Retry";
    button.addEventListener("click", retry);
    wrapper.appendChild(button);
  }

  element.appendChild(wrapper);
};

const requestJson = async (url) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) throw new Error(`Request failed with status ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
};

const updateClock = () => {
  const now = new Date();
  elements.clock.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  elements.clock.dateTime = now.toISOString();
};

const renderScene = (tracks) => {
  elements.scene.replaceChildren();
  elements.targetCount.textContent = `${tracks.length} active`;
  if (!tracks.length) {
    setError(elements.scene, "No targets in frame", null);
    return;
  }

  tracks.forEach((track) => {
    const item = document.createElement("article");
    item.className = "scene-item";

    const thumbnail = document.createElement("div");
    thumbnail.className = "scene-thumbnail";
    const image = document.createElement("img");
    image.src = `/thumb/${track.track_id}`;
    image.alt = `${track.class_name} track ${track.track_id}`;
    image.loading = "lazy";
    image.addEventListener("error", () => thumbnail.classList.add("is-unavailable"));
    const placeholder = document.createElement("span");
    placeholder.className = "thumbnail-placeholder";
    placeholder.textContent = "NO CROP";
    thumbnail.append(image, placeholder);

    const details = document.createElement("div");
    details.className = "scene-details";
    const header = document.createElement("div");
    header.className = "scene-header";
    const id = document.createElement("span");
    id.className = "scene-id";
    id.textContent = trkId(track.track_id);
    const className = document.createElement("span");
    className.className = "scene-class";
    className.textContent = track.class_name;
    header.append(id, className);

    const description = document.createElement("p");
    description.className = "scene-description";
    description.textContent = track.description || "Awaiting description…";
    details.append(header, description);
    item.append(thumbnail, details);
    elements.scene.appendChild(item);
  });
};

const renderStats = (data) => {
  const people = Number(data.total_people) || 0;
  const objects = Number(data.total_objects) || 0;
  const peak = data.busiest_hour === null ? "No peak yet" : `${pad(data.busiest_hour)}:00`;
  elements.stats.replaceChildren();

  const peopleValue = document.createElement("strong");
  peopleValue.textContent = people;
  const objectsValue = document.createElement("strong");
  objectsValue.textContent = objects;
  const peakLabel = document.createElement("span");
  peakLabel.className = "peak-label";
  peakLabel.textContent = `Peak ${peak}`;
  elements.stats.append(peopleValue, " people ", objectsValue, " objects ", peakLabel);

  const max = Math.max(1, ...data.hourly.map((hour) => hour.count));
  const byHour = Object.fromEntries(data.hourly.map((hour) => [hour.hour, hour.count]));
  elements.hourlyChart.replaceChildren();
  for (let hour = 0; hour < 24; hour += 1) {
    const count = byHour[hour] || 0;
    const bar = document.createElement("div");
    bar.className = "hourly-bar";
    bar.style.height = `${Math.max(4, (count / max) * 100)}%`;
    bar.title = `${pad(hour)}:00 — ${count}`;
    bar.setAttribute("aria-label", `${pad(hour)}:00, ${count} events`);
    elements.hourlyChart.appendChild(bar);
  }
};

const renderVisits = (visits) => {
  elements.visits.replaceChildren();
  if (!visits.length) {
    setError(elements.visits, "No activity recorded", null);
    return;
  }

  visits.forEach((visit) => {
    const row = document.createElement("article");
    row.className = "visit-row";
    const head = document.createElement("div");
    head.className = "visit-head";
    const id = document.createElement("span");
    id.className = "visit-id";
    id.textContent = trkId(visit.track_id);
    head.append(id, ` · ${visit.class_name} · ${timeOf(visit.start)} · ${visit.duration}s`);

    const text = document.createElement("p");
    text.className = "visit-text";
    text.textContent = visit.narrative.length ? visit.narrative.join(" — ") : "No description captured";
    row.append(head, text);
    elements.visits.appendChild(row);
  });
};

const refreshScene = async () => {
  try {
    renderScene((await requestJson("/api/scene")).tracks);
  } catch (error) {
    setError(elements.scene, "Unable to load active targets", refreshScene);
  }
};

const refreshStats = async () => {
  try {
    renderStats(await requestJson("/api/stats"));
  } catch (error) {
    setError(elements.stats, "Unable to load activity", refreshStats);
  }
};

const refreshVisits = async () => {
  try {
    renderVisits((await requestJson("/api/visits")).visits);
  } catch (error) {
    setError(elements.visits, "Unable to load visit history", refreshVisits);
  }
};

const refreshStatus = async () => {
  try {
    await requestJson("/api/status");
    setStatus(feedAvailable ? "is-live" : "is-syncing", feedAvailable ? "Live" : "Waiting for video");
  } catch (error) {
    setStatus("is-offline", "Offline");
  }
};

const refreshAll = async () => {
  setStatus("is-syncing", "Syncing");
  await Promise.all([refreshScene(), refreshStats(), refreshVisits(), refreshStatus()]);
};

elements.liveFeed.addEventListener("load", () => {
  feedAvailable = true;
  elements.feedLoading.hidden = true;
  elements.feedError.hidden = true;
  setStatus("is-live", "Live");
});
elements.liveFeed.addEventListener("error", () => {
  feedAvailable = false;
  elements.feedLoading.hidden = true;
  elements.feedError.hidden = false;
  setStatus("is-offline", "Video offline");
});

setInterval(updateClock, 1000);
setInterval(refreshAll, 5000);
setInterval(refreshStatus, 15000);

updateClock();
setLoading(elements.scene, "Loading targets…");
setLoading(elements.stats, "Loading activity…");
setLoading(elements.visits, "Loading visits…");
refreshAll();
