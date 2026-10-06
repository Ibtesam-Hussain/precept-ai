"""
Thread-safe holder for the latest annotated camera frame, shared between
the tracker thread (writer) and the dashboard's video endpoint (reader).
This is only possible because tracker and dashboard now run in one process.
"""

import threading
from typing import Optional

_lock = threading.Lock()
_latest_jpeg: Optional[bytes] = None


def set_frame(jpeg_bytes: bytes) -> None:
    global _latest_jpeg
    with _lock:
        _latest_jpeg = jpeg_bytes


def get_frame() -> Optional[bytes]:
    with _lock:
        return _latest_jpeg