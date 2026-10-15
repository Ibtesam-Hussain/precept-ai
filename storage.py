"""
SQLite storage for events and descriptions. Replaces the JSONL files.
Uses WAL mode so the dashboard can read concurrently while the pipeline writes.
"""

import sqlite3
import threading
import time
import datetime

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


def get_latest_description(track_id):
    with _lock:
        cur = _conn.execute(
            "SELECT description FROM descriptions WHERE track_id = ? ORDER BY id DESC LIMIT 1",
            (track_id,)
        )
        row = cur.fetchone()
    return row[0] if row else None


def get_active_tracks():
    """Tracks whose most recent event isn't track_exited — i.e. currently in frame."""
    with _lock:
        cur = _conn.execute("""
            SELECT e.track_id, e.class_name, e.event_type
            FROM events e
            INNER JOIN (
                SELECT track_id, MAX(id) AS max_id FROM events GROUP BY track_id
            ) latest ON e.track_id = latest.track_id AND e.id = latest.max_id
            WHERE e.event_type != 'track_exited'
            ORDER BY e.track_id
        """)
        rows = cur.fetchall()

    return [
        {
            "track_id": track_id,
            "class_name": class_name,
            "event_type": event_type,
            "description": get_latest_description(track_id),
        }
        for track_id, class_name, event_type in rows
    ]


def get_today_stats():
    start_of_day = datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp()

    with _lock:
        by_class_cur = _conn.execute(
            "SELECT class_name, COUNT(*) FROM events "
            "WHERE event_type = 'track_new' AND timestamp >= ? GROUP BY class_name",
            (start_of_day,)
        )
        by_class = dict(by_class_cur.fetchall())

        hour_cur = _conn.execute(
            "SELECT CAST(strftime('%H', timestamp, 'unixepoch', 'localtime') AS INTEGER) AS hour, COUNT(*) "
            "FROM events WHERE event_type = 'track_new' AND timestamp >= ? "
            "GROUP BY hour ORDER BY hour",
            (start_of_day,)
        )
        hourly = hour_cur.fetchall()

    total_people = by_class.get("person", 0)
    total_objects = sum(c for name, c in by_class.items() if name != "person")
    busiest = max(hourly, key=lambda x: x[1]) if hourly else None

    return {
        "total_people": total_people,
        "total_objects": total_objects,
        "busiest_hour": busiest[0] if busiest else None,
        "hourly": [{"hour": h, "count": c} for h, c in hourly],
    }


def get_track_visits(limit=20):
    """Groups raw events + descriptions into one card per track lifecycle
    (track_new -> ... -> track_exited), not one row per raw event."""
    with _lock:
        ev_cur = _conn.execute(
            "SELECT track_id, event_type, class_name, timestamp FROM events "
            "ORDER BY id DESC LIMIT 300"
        )
        events = list(reversed(ev_cur.fetchall()))

        desc_cur = _conn.execute(
            "SELECT track_id, description, timestamp FROM descriptions "
            "ORDER BY id DESC LIMIT 300"
        )
        descriptions = list(reversed(desc_cur.fetchall()))

    desc_by_track = {}
    for track_id, description, ts in descriptions:
        desc_by_track.setdefault(track_id, []).append((ts, description))

    visits = []
    open_visits = {}

    for track_id, event_type, class_name, ts in events:
        if event_type == "track_new":
            open_visits[track_id] = {"start": ts, "class_name": class_name}
        elif event_type == "track_exited" and track_id in open_visits:
            v = open_visits.pop(track_id)
            narrative = [d for (dts, d) in desc_by_track.get(track_id, []) if v["start"] <= dts <= ts]
            visits.append({
                "track_id": track_id,
                "class_name": v["class_name"],
                "start": v["start"],
                "duration": round(ts - v["start"], 1),
                "narrative": narrative,
            })

    visits.sort(key=lambda v: v["start"], reverse=True)
    return visits[:limit]