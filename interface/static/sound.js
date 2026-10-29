(() => {
  "use strict";

  const toggle = document.querySelector("[data-sound-toggle]");
  if (!toggle) return;

  const STORE = "precept.sound.enabled";
  let enabled = false;
  let audioContext = null;
  let lastSoundAt = 0;
  try { enabled = localStorage.getItem(STORE) === "true"; } catch (error) { /* storage unavailable: stay muted */ }
  toggle.checked = enabled;

  const activateAudio = () => {
    if (!enabled) return;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      enabled = false;
      toggle.checked = false;
      toggle.disabled = true;
      toggle.title = "Web Audio is not supported by this browser";
      try { localStorage.setItem(STORE, "false"); } catch (error) { /* storage unavailable */ }
      return;
    }
    if (!audioContext) audioContext = new AudioContextClass();
    if (audioContext.state === "suspended") audioContext.resume().catch(() => {});
  };

  const play = (event) => {
    if (!enabled || !audioContext || audioContext.state !== "running") return;
    const now = performance.now();
    if (now - lastSoundAt < 500) return;
    if (event.detail.event_type !== "track_new" && event.detail.event_type !== "track_exited") return;
    lastSoundAt = now;

    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    const start = audioContext.currentTime;
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(
      event.detail.event_type === "track_new" ? 880 : 440,
      start
    );
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.045, start + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.14);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start(start);
    oscillator.stop(start + 0.15);
  };

  toggle.addEventListener("change", () => {
    enabled = toggle.checked;
    try { localStorage.setItem(STORE, String(enabled)); } catch (error) { /* storage unavailable */ }
    if (enabled) activateAudio();
    else if (audioContext && audioContext.state === "running") audioContext.suspend().catch(() => {});
  });
  document.addEventListener("pointerdown", activateAudio, { once: true });
  document.addEventListener("keydown", activateAudio, { once: true });
  document.addEventListener("precept:event", play);
})();
