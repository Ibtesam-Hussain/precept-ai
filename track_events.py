from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional


class EventType(str, Enum):
    TRACK_NEW = "track_new"
    TRACK_STABLE = "track_stable"
    TRACK_APPEARANCE_CHANGED = "track_appearance_changed"
    TRACK_EXITED = "track_exited"


@dataclass
class TrackEvent:
    track_id: int
    event_type: EventType
    class_name: str
    bbox: tuple
    confidence: float
    frame_idx: int
    crop: Optional[object] = None  # numpy array, only present on events that need one
    timestamp: float = field(default_factory=time)
    extra: Optional[dict] = None