// HUD controls: sensor modes and layer toggles. Independent of app.js (data/rendering).
(() => {
  const stage = document.getElementById("stage");
  const classification = document.getElementById("classification");
  const layersCount = document.getElementById("layers-count");
  const modeButtons = [...document.querySelectorAll(".mode")];
  const layerInputs = [...document.querySelectorAll("input[data-layer]")];
  const MODES = ["std", "nvg", "flir", "crt"];
  const LAYER_CLASS = { fill: "fill-frame" };   // "on = add class"; all others are "off = hide-<name>"
  const STORE = "precept.hud.v1";

  const load = () => {
    try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch (error) { return {}; }
  };
  const save = (state) => {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (error) { /* storage unavailable: ignore */ }
  };
  const state = load();

  const setMode = (mode) => {
    if (!MODES.includes(mode)) mode = "std";
    stage.dataset.sensor = mode;
    modeButtons.forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-checked", String(active));
    });
    classification.textContent = mode === "std" ? "CAM-01 // LIVE FEED" : `CAM-01 // LIVE FEED // ${mode.toUpperCase()}`;
    state.mode = mode;
    save(state);
  };

  const applyLayer = (name, on) => {
    if (LAYER_CLASS[name]) document.body.classList.toggle(LAYER_CLASS[name], on);
    else document.body.classList.toggle(`hide-${name}`, !on);
  };
  const updateCount = () => {
    const visible = layerInputs.filter((input) => input.checked && input.dataset.layer !== "fill").length;
    layersCount.textContent = String(visible);
  };

  modeButtons.forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  layerInputs.forEach((input) => {
    const name = input.dataset.layer;
    if (typeof state.layers?.[name] === "boolean") input.checked = state.layers[name];
    applyLayer(name, input.checked);
    input.addEventListener("change", () => {
      applyLayer(name, input.checked);
      state.layers = { ...(state.layers || {}), [name]: input.checked };
      save(state);
      updateCount();
    });
  });

  // Keys 1-4 switch sensor mode (ignored while typing in a field)
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const index = Number(event.key) - 1;
    if (index >= 0 && index < MODES.length) setMode(MODES[index]);
  });

  updateCount();
  setMode(state.mode);
})();