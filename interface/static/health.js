// Header health strip: polls /api/system and renders FPS, LLM latency, queue and job status.
(() => {
  const POLL_MS = 2000;
  const health = document.getElementById("health");
  const chips = {
    fps: { chip: document.getElementById("chip-fps"), value: document.getElementById("h-fps") },
    llm: { chip: document.getElementById("chip-llm"), value: document.getElementById("h-llm") },
    queue: { chip: document.getElementById("chip-queue"), value: document.getElementById("h-queue") },
    jobs: { chip: document.getElementById("chip-jobs"), value: document.getElementById("h-jobs") },
  };

  const show = (key, text, state, title = "") => {
    chips[key].value.textContent = text;
    chips[key].chip.dataset.state = state;
    chips[key].chip.title = title;
  };
  const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

  const render = (data) => {
    health.classList.remove("is-stale");
    const uptime = num(data.uptime_s);
    const uptimeText = uptime === null ? "" : `Uptime ${Math.floor(uptime / 60)}m ${uptime % 60}s`;

    const fps = num(data.fps);
    if (fps === null) show("fps", "--", "dim", "No frames processed yet");
    else show("fps", fps.toFixed(1), fps >= 15 ? "ok" : fps >= 5 ? "warn" : "bad", "Tracker frames per second");

    const latency = num(data.llm_latency_ms);
    if (latency === null) show("llm", "--", "dim", "No successful LLM request yet this session");
    else show("llm", `${(latency / 1000).toFixed(1)}s`, latency < 3000 ? "ok" : latency < 8000 ? "warn" : "bad", "Average of recent successful LLM requests");

    const jobs = data.jobs || {};
    const pending = num(jobs.pending) ?? 0;
    const retrying = num(jobs.retrying) ?? 0;
    const failed = num(jobs.failed) ?? 0;
    const done = num(jobs.done) ?? 0;

    const queued = pending + retrying;
    show("queue", String(queued), queued === 0 ? "ok" : queued <= 20 ? "warn" : "bad", "Description jobs waiting or retrying");

    const parts = [];
    if (pending) parts.push(`${pending} PEND`);
    if (retrying) parts.push(`${retrying} RETRY`);
    if (failed) parts.push(`${failed} FAIL`);
    const state = failed ? "bad" : retrying ? "warn" : "ok";
    show("jobs", parts.length ? parts.join(" · ") : "IDLE", state,
      `Pending ${pending}, retrying ${retrying}, failed ${failed}, done ${done} (all-time). ${uptimeText}`.trim());
  };

  const markStale = () => {
    health.classList.add("is-stale");
    Object.keys(chips).forEach((key) => show(key, "--", "dim", "System status unavailable"));
  };

  const poll = async () => {
    if (document.hidden) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch("/api/system", { signal: controller.signal });
      if (!response.ok) throw new Error(`status ${response.status}`);
      render(await response.json());
    } catch (error) {
      markStale();
    } finally {
      clearTimeout(timeout);
    }
  };

  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  setInterval(poll, POLL_MS);
  poll();
})();