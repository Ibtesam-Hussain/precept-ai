// HUD controls: sensor modes and layer toggles. Independent of app.js (data/rendering).
(() => {
  const stage = document.getElementById("stage");
  const classification = document.getElementById("classification");
  const layersCount = document.getElementById("layers-count");
  const modeButtons = [...document.querySelectorAll(".mode")];
  const themeButtons = [...document.querySelectorAll(".theme-swatch")];
  const layerInputs = [...document.querySelectorAll("input[data-layer]")];
  const MODES = ["std", "nvg", "flir", "crt"];
  const THEMES = ["cyan", "amber", "green"];
  const LAYER_CLASS = { fill: "fill-frame", perf: "perf-mode" };
  const STORE = "precept.hud.v1";
  const THEME_STORE = "precept.hud.theme";

  const load = () => {
    try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch (error) { return {}; }
  };
  const save = (state) => {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (error) { /* storage unavailable: ignore */ }
  };
  const state = load();
  const savedTheme = (() => {
    try {
      const theme = localStorage.getItem(THEME_STORE);
      return THEMES.includes(theme) ? theme : "cyan";
    } catch (error) {
      return "cyan";
    }
  })();

  const setMode = (mode) => {
    if (!MODES.includes(mode)) mode = "std";
    stage.dataset.sensor = mode;
    modeButtons.forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-checked", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    classification.textContent = mode === "std" ? "CAM-01 // LIVE FEED" : `CAM-01 // LIVE FEED // ${mode.toUpperCase()}`;
    state.mode = mode;
    save(state);
  };

  const setTheme = (theme, persist = true) => {
    if (!THEMES.includes(theme)) theme = "cyan";
    document.documentElement.dataset.theme = theme;
    themeButtons.forEach((button) => {
      const active = button.dataset.theme === theme;
      button.setAttribute("aria-checked", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    if (persist) {
      try { localStorage.setItem(THEME_STORE, theme); } catch (error) { /* storage unavailable: ignore */ }
    }
  };

  const applyLayer = (name, on) => {
    if (LAYER_CLASS[name]) document.body.classList.toggle(LAYER_CLASS[name], on);
    else document.body.classList.toggle(`hide-${name}`, !on);
    if (name === "perf") {
      document.documentElement.classList.toggle("perf-mode", on);
      document.dispatchEvent(new Event("precept:perfchange"));
    }
  };
  const updateCount = () => {
    const visible = layerInputs.filter((input) => input.checked && input.dataset.layer !== "fill").length;
    layersCount.textContent = String(visible);
  };

  modeButtons.forEach((button, index) => {
    button.addEventListener("click", () => setMode(button.dataset.mode));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const next = (index + direction + modeButtons.length) % modeButtons.length;
      setMode(modeButtons[next].dataset.mode);
      modeButtons[next].focus();
    });
  });
  themeButtons.forEach((button, index) => {
    button.addEventListener("click", () => setTheme(button.dataset.theme));
    button.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const next = (index + direction + themeButtons.length) % themeButtons.length;
      setTheme(themeButtons[next].dataset.theme);
      themeButtons[next].focus();
    });
  });
  layerInputs.forEach((input) => {
    const name = input.dataset.layer;
    if (name === "perf" && typeof state.layers?.perf !== "boolean") {
      input.checked = Number.isFinite(navigator.hardwareConcurrency)
        && navigator.hardwareConcurrency <= 4;
    } else if (typeof state.layers?.[name] === "boolean") {
      input.checked = state.layers[name];
    }
    applyLayer(name, input.checked);
    input.addEventListener("change", () => {
      applyLayer(name, input.checked);
      state.layers = { ...(state.layers || {}), [name]: input.checked };
      save(state);
      updateCount();
    });
  });

  setTheme(savedTheme, false);

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