"""
SQLite storage for events and descriptions. Replaces the JSONL files.
Uses WAL mode so the dashboard can read concurrently while the pipeline writes.
"""

import ast
from dataclasses import dataclass
import sqlite3
import threading
import time
import datetime
from typing import List, Optional

import cv2
import numpy as np

from track_events import EventType, TrackEvent

DB_PATH = "precept.db"
MAX_DESCRIPTION_ATTEMPTS = 5
RETRY_BASE_DELAY_SECONDS = 5
RETRY_MAX_DELAY_SECONDS = 300
_lock = threading.Lock()


@dataclass(frozen=True)
class DescriptionJob:
    job_id: int
    event: TrackEvent


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
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
                timestamp REAL,
                event_id INTEGER REFERENCES events(id)
            )
        """)
        description_columns = {
            row[1] for row in _conn.execute("PRAGMA table_info(descriptions)")
        }
        if "event_id" not in description_columns:
            _conn.execute(
                "ALTER TABLE descriptions "
                "ADD COLUMN event_id INTEGER REFERENCES events(id)"
            )
        _conn.execute("""
            CREATE TABLE IF NOT EXISTS description_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL UNIQUE REFERENCES events(id),
                crop_jpeg BLOB,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'in_progress', 'done', 'failed')
                ),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        job_columns = {
            row[1]
            for row in _conn.execute("PRAGMA table_info(description_jobs)")
        }
        if "next_attempt_at" not in job_columns:
            _conn.execute(
                "ALTER TABLE description_jobs "
                "ADD COLUMN next_attempt_at REAL NOT NULL DEFAULT 0"
            )
        _conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_description_jobs_status
            ON description_jobs(status, next_attempt_at, id)
        """)
        _conn.commit()


def _encode_description_crop(crop) -> Optional[bytes]:
    if crop is None:
        return None

    height, width = crop.shape[:2]
    scale = min(384 / max(height, width), 1.0)
    if scale < 1.0:
        crop = cv2.resize(
            crop,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    ok, encoded = cv2.imencode(
        ".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70]
    )
    return encoded.tobytes() if ok else None


def persist_event(event: TrackEvent) -> None:
    """Persist an event and, when relevant, its description job atomically."""
    description_event = event.event_type in (
        EventType.TRACK_STABLE,
        EventType.TRACK_APPEARANCE_CHANGED,
    )
    crop_jpeg = _encode_description_crop(event.crop) if description_event else None
    job_status = "pending" if crop_jpeg is not None else "failed"
    job_error = None if crop_jpeg is not None else "No valid crop available"
    now = time.time()

    with _lock:
        with _conn:
            cursor = _conn.execute(
                "INSERT INTO events (track_id, event_type, class_name, bbox, confidence, frame_idx, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.track_id,
                    event.event_type.value,
                    event.class_name,
                    str(event.bbox),
                    event.confidence,
                    event.frame_idx,
                    event.timestamp,
                ),
            )
            if description_event:
                _conn.execute(
                    "INSERT INTO description_jobs "
                    "(event_id, crop_jpeg, status, last_error, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        cursor.lastrowid,
                        crop_jpeg,
                        job_status,
                        job_error,
                        now,
                        now,
                    ),
                )


def recover_interrupted_description_jobs() -> int:
    """Requeue jobs left in progress by a previous process run."""
    with _lock, _conn:
        now = time.time()
        _conn.execute(
            "UPDATE description_jobs "
            "SET status = 'failed', last_error = 'Maximum attempts exceeded', "
            "updated_at = ? "
            "WHERE status = 'in_progress' AND attempts >= ?",
            (now, MAX_DESCRIPTION_ATTEMPTS),
        )
        cursor = _conn.execute(
            "UPDATE description_jobs "
            "SET status = 'pending', last_error = NULL, next_attempt_at = 0, "
            "updated_at = ? "
            "WHERE status = 'in_progress' AND attempts < ?",
            (now, MAX_DESCRIPTION_ATTEMPTS),
        )
        return cursor.rowcount


def claim_description_jobs(limit: int = 8) -> List[DescriptionJob]:
    """Atomically claim pending jobs and reconstruct their TrackEvents."""
    if limit <= 0:
        return []

    with _lock:
        try:
            _conn.execute("BEGIN IMMEDIATE")
            rows = _conn.execute(
                """
                SELECT j.id, j.crop_jpeg, e.track_id, e.event_type,
                       e.class_name, e.bbox, e.confidence, e.frame_idx,
                       e.timestamp
                FROM description_jobs j
                JOIN events e ON e.id = j.event_id
                WHERE j.status = 'pending' AND j.next_attempt_at <= ?
                ORDER BY j.id
                LIMIT ?
                """,
                (time.time(), limit),
            ).fetchall()
            if rows:
                job_ids = [row[0] for row in rows]
                placeholders = ",".join("?" for _ in job_ids)
                _conn.execute(
                    f"UPDATE description_jobs "
                    f"SET status = 'in_progress', attempts = attempts + 1, "
                    f"updated_at = ? WHERE id IN ({placeholders})",
                    (time.time(), *job_ids),
                )
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise

    jobs = []
    for row in rows:
        (
            job_id,
            crop_jpeg,
            track_id,
            event_type,
            class_name,
            bbox,
            confidence,
            frame_idx,
            timestamp,
        ) = row
        if crop_jpeg is None:
            fail_description_jobs([job_id], "Stored crop is missing")
            continue
        crop = cv2.imdecode(
            np.frombuffer(crop_jpeg, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if crop is None:
            fail_description_jobs(
                [job_id],
                "Stored crop JPEG could not be decoded",
            )
            continue

        jobs.append(DescriptionJob(
            job_id=job_id,
            event=TrackEvent(
                track_id=track_id,
                event_type=EventType(event_type),
                class_name=class_name,
                bbox=ast.literal_eval(bbox),
                confidence=confidence,
                frame_idx=frame_idx,
                crop=crop,
                timestamp=timestamp,
            ),
        ))

    return jobs


def complete_description_job(job_id: int, event: TrackEvent, description: str) -> None:
    now = time.time()
    with _lock, _conn:
        job = _conn.execute(
            "SELECT event_id FROM description_jobs "
            "WHERE id = ? AND status = 'in_progress'",
            (job_id,),
        ).fetchone()
        if job is None:
            raise RuntimeError(f"Description job {job_id} is not in progress")

        cursor = _conn.execute(
            "UPDATE description_jobs "
            "SET status = 'done', last_error = NULL, updated_at = ? "
            "WHERE id = ? AND status = 'in_progress'",
            (now, job_id),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Description job {job_id} is not in progress")
        _conn.execute(
            "INSERT INTO descriptions "
            "(track_id, event_type, class_name, description, timestamp, event_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.track_id,
                event.event_type.value,
                event.class_name,
                description,
                now,
                job[0],
            ),
        )


def fail_description_jobs(
    job_ids: List[int],
    error: str,
    retryable: bool = False,
    retry_after_seconds: Optional[float] = None,
) -> tuple[int, int]:
    if not job_ids:
        return 0, 0

    retrying = 0
    failed = 0
    now = time.time()
    with _lock:
        with _conn:
            for job_id in job_ids:
                row = _conn.execute(
                    "SELECT attempts FROM description_jobs "
                    "WHERE id = ? AND status = 'in_progress'",
                    (job_id,),
                ).fetchone()
                if row is None:
                    continue

                attempts = row[0]
                should_retry = retryable and attempts < MAX_DESCRIPTION_ATTEMPTS
                if should_retry:
                    backoff = min(
                        RETRY_BASE_DELAY_SECONDS * (2 ** (attempts - 1)),
                        RETRY_MAX_DELAY_SECONDS,
                    )
                    if retry_after_seconds is not None:
                        backoff = max(backoff, retry_after_seconds)
                    _conn.execute(
                        "UPDATE description_jobs "
                        "SET status = 'pending', last_error = ?, "
                        "next_attempt_at = ?, updated_at = ? "
                        "WHERE id = ? AND status = 'in_progress'",
                        (error, now + backoff, now, job_id),
                    )
                    retrying += 1
                else:
                    _conn.execute(
                        "UPDATE description_jobs "
                        "SET status = 'failed', last_error = ?, updated_at = ? "
                        "WHERE id = ? AND status = 'in_progress'",
                        (error, now, job_id),
                    )
                    failed += 1

    return retrying, failed


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


def get_job_counts():
    """Return description-job counts grouped by lifecycle and retry state."""
    with _lock:
        row = _conn.execute("""
            SELECT
                COALESCE(SUM(CASE
                    WHEN status = 'pending' AND attempts = 0 THEN 1
                    ELSE 0
                END), 0),
                COALESCE(SUM(CASE
                    WHEN status NOT IN ('done', 'failed') AND attempts > 0 THEN 1
                    ELSE 0
                END), 0),
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END), 0)
            FROM description_jobs
        """).fetchone()
    return {
        "pending": row[0],
        "retrying": row[1],
        "failed": row[2],
        "done": row[3],
    }


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
            "SELECT id, track_id, event_type, class_name, timestamp FROM events "
            "ORDER BY id DESC LIMIT 300"
        )
        events = list(reversed(ev_cur.fetchall()))

        desc_cur = _conn.execute(
            "SELECT track_id, description, timestamp, event_id FROM descriptions "
            "ORDER BY id DESC LIMIT 300"
        )
        descriptions = list(reversed(desc_cur.fetchall()))

    descriptions_by_event = {}
    legacy_descriptions_by_track = {}
    for track_id, description, ts, event_id in descriptions:
        if event_id is not None:
            descriptions_by_event.setdefault(event_id, []).append(description)
        else:
            legacy_descriptions_by_track.setdefault(track_id, []).append(
                (ts, description)
            )

    visits = []
    open_visits = {}

    for event_id, track_id, event_type, class_name, ts in events:
        if event_type == "track_new":
            open_visits[track_id] = {
                "start": ts,
                "start_event_id": event_id,
                "class_name": class_name,
            }
        elif event_type == "track_exited" and track_id in open_visits:
            v = open_visits.pop(track_id)
            narrative = []
            for (
                description_event_id,
                event_track_id,
                description_event_type,
                _,
                _,
            ) in events:
                if (
                    event_track_id == track_id
                    and v["start_event_id"] <= description_event_id < event_id
                    and description_event_type in (
                        "track_stable",
                        "track_appearance_changed",
                    )
                ):
                    narrative.extend(
                        descriptions_by_event.get(description_event_id, [])
                    )
            narrative.extend(
                description
                for description_ts, description in
                legacy_descriptions_by_track.get(track_id, [])
                if v["start"] <= description_ts <= ts
            )
            visits.append({
                "track_id": track_id,
                "class_name": v["class_name"],
                "start": v["start"],
                "duration": round(ts - v["start"], 1),
                "narrative": narrative,
            })

    visits.sort(key=lambda v: v["start"], reverse=True)
    return visits[:limit]