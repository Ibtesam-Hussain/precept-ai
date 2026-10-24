"""Thread-safe in-memory snapshots for tracker and LLM runtime telemetry."""

from collections import deque
import threading
import time
from typing import Any, Dict, Optional

_lock = threading.Lock()
_frame_times = deque()
_llm_latencies = deque(maxlen=20)
_tracks_snapshot: Optional[Dict[str, Any]] = None
started_at = time.monotonic()


def record_frame(timestamp: Optional[float] = None) -> None:
    """Record one processed frame using a monotonic timestamp."""
    now = time.monotonic() if timestamp is None else timestamp
    with _lock:
        _frame_times.append(now)
        cutoff = now - 2.0
        while len(_frame_times) > 1 and _frame_times[0] < cutoff:
            _frame_times.popleft()


def get_fps(timestamp: Optional[float] = None) -> Optional[float]:
    with _lock:
        now = time.monotonic() if timestamp is None else timestamp
        cutoff = now - 2.0
        while len(_frame_times) > 1 and _frame_times[0] < cutoff:
            _frame_times.popleft()
        if _frame_times and _frame_times[-1] < cutoff:
            _frame_times.clear()
        if len(_frame_times) < 2:
            return None
        elapsed = _frame_times[-1] - _frame_times[0]
        if elapsed <= 0:
            return None
        return (len(_frame_times) - 1) / elapsed


def record_llm_latency(seconds: float) -> None:
    """Record one successful LLM request duration in seconds."""
    with _lock:
        _llm_latencies.append(seconds)


def get_llm_latency_ms() -> Optional[float]:
    with _lock:
        if not _llm_latencies:
            return None
        return sum(_llm_latencies) / len(_llm_latencies) * 1000


def get_last_llm_latency_ms() -> Optional[float]:
    with _lock:
        if not _llm_latencies:
            return None
        return _llm_latencies[-1] * 1000


def publish_tracks(
    seq: int,
    frame_w: int,
    frame_h: int,
    tracks: list[dict],
    timestamp: Optional[float] = None,
) -> None:
    """Replace the latest track snapshot without retaining history."""
    snapshot = {
        "seq": seq,
        "frame": {"w": frame_w, "h": frame_h},
        "ts": time.time() if timestamp is None else timestamp,
        "tracks": tracks,
    }
    with _lock:
        global _tracks_snapshot
        _tracks_snapshot = snapshot


def get_tracks_snapshot() -> Dict[str, Any]:
    with _lock:
        if _tracks_snapshot is None:
            return {"seq": 0, "frame": {"w": 0, "h": 0}, "ts": 0, "tracks": []}
        return _tracks_snapshot
