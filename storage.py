"""
SQLite storage for events and descriptions. Replaces the JSONL files.
Uses WAL mode so the dashboard can read concurrently while the pipeline writes.
"""

import sqlite3
import threading
import time

DB_PATH = "precept.db"
_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


_conn = _connect()


def init_db():
    with _lock:
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                event_type TEXT,
                class_name TEXT,
                bbox TEXT,
                confidence REAL,
                frame_idx INTEGER,
                timestamp REAL
            )
        """)
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS descriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                event_type TEXT,
                class_name TEXT,
                description TEXT,
                timestamp REAL
            )
        """)
        _conn.commit()


def insert_event(e) -> None:
    """e is a TrackEvent."""
    with _lock:
        _conn.execute(
            "INSERT INTO events (track_id, event_type, class_name, bbox, confidence, frame_idx, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (e.track_id, e.event_type.value, e.class_name, str(e.bbox),
             e.confidence, e.frame_idx, e.timestamp),
        )
        _conn.commit()


def insert_description(track_id, event_type, class_name, description, timestamp=None) -> None:
    with _lock:
        _conn.execute(
            "INSERT INTO descriptions (track_id, event_type, class_name, description, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (track_id, event_type, class_name, description, timestamp or time.time()),
        )
        _conn.commit()


def get_recent_descriptions(limit=30):
    with _lock:
        cur = _conn.execute(
            "SELECT track_id, event_type, class_name, description, timestamp "
            "FROM descriptions ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cur.fetchall()
    return [
        {"track_id": r[0], "event_type": r[1], "class_name": r[2], "description": r[3], "timestamp": r[4]}
        for r in rows
    ]


def get_recent_events(limit=50):
    with _lock:
        cur = _conn.execute(
            "SELECT track_id, event_type, class_name, bbox, confidence, frame_idx, timestamp "
            "FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cur.fetchall()
    return [
        {"track_id": r[0], "event_type": r[1], "class_name": r[2], "bbox": r[3],
         "confidence": r[4], "frame_idx": r[5], "timestamp": r[6]}
        for r in rows
    ]