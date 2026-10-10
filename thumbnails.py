"""
Thread-safe per-track thumbnail store. Holds the latest JPEG crop for each
currently-active track so the dashboard's Current Scene panel can show an
image alongside each description. Cleared when a track exits.
"""

import threading
from typing import Dict, Iterable, Optional, Set

_lock = threading.Lock()
_thumbs: Dict[int, bytes] = {}
_active_track_ids: Set[int] = set()


def set_thumb(track_id: int, jpeg_bytes: bytes) -> None:
    with _lock:
        _thumbs[track_id] = jpeg_bytes


def get_thumb(track_id: int) -> Optional[bytes]:
    with _lock:
        return _thumbs.get(track_id)


def clear_thumb(track_id: int) -> None:
    with _lock:
        _thumbs.pop(track_id, None)


def set_active_track_ids(track_ids: Iterable[int]) -> None:
    with _lock:
        _active_track_ids.clear()
        _active_track_ids.update(track_ids)


def get_active_track_ids() -> Set[int]:
    with _lock:
        return set(_active_track_ids)