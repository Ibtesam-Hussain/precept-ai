from dataclasses import dataclass
from typing import Dict, Optional
import imagehash
from PIL import Image

from track_events import TrackEvent, EventType


@dataclass
class TrackState:
    track_id: int
    class_name: str
    first_frame: int
    last_frame: int
    frames_seen: int = 1
    emitted_stable: bool = False
    last_described_hash: Optional[imagehash.ImageHash] = None
    last_description_frame: int = 0
    last_bbox: tuple = None


class TrackStateManager:
    def __init__(self, stable_frames=10, exit_after=15,
                 appearance_hash_threshold=30, recheck_cooldown_frames=30):
        self.stable_frames = stable_frames
        self.exit_after = exit_after
        self.appearance_hash_threshold = appearance_hash_threshold
        self.recheck_cooldown_frames = recheck_cooldown_frames
        self.tracks: Dict[int, TrackState] = {}

    def update(self, track_id, class_name, bbox, confidence, crop, frame_idx):
        events = []
        state = self.tracks.get(track_id)

        if state is None:
            state = TrackState(track_id, class_name, frame_idx, frame_idx)
            self.tracks[track_id] = state
            events.append(TrackEvent(
                track_id=track_id, event_type=EventType.TRACK_NEW, class_name=class_name,
                bbox=bbox, confidence=confidence, frame_idx=frame_idx, crop=crop,
            ))
        else:
            state.frames_seen += 1
            state.last_frame = frame_idx
            state.last_bbox = bbox

        if not state.emitted_stable and state.frames_seen >= self.stable_frames:
            state.emitted_stable = True
            events.append(TrackEvent(
                track_id=track_id, event_type=EventType.TRACK_STABLE, class_name=class_name,
                bbox=bbox, confidence=confidence, frame_idx=frame_idx, crop=crop,
            ))
            state.last_described_hash = self._hash_crop(crop)
            state.last_description_frame = frame_idx

        elif state.emitted_stable and (frame_idx - state.last_description_frame) >= self.recheck_cooldown_frames:
            new_hash = self._hash_crop(crop)
            if new_hash and state.last_described_hash:
                if new_hash - state.last_described_hash >= self.appearance_hash_threshold:
                    state.last_described_hash = new_hash
                    state.last_description_frame = frame_idx
                    events.append(TrackEvent(
                        track_id=track_id, event_type=EventType.TRACK_APPEARANCE_CHANGED,
                        class_name=class_name, bbox=bbox, confidence=confidence,
                        frame_idx=frame_idx, crop=crop,
                    ))
        return events

    def sweep_exits(self, current_frame_idx):
        events, dead_ids = [], []
        for track_id, state in self.tracks.items():
            if current_frame_idx - state.last_frame >= self.exit_after:
                events.append(TrackEvent(
                    track_id=track_id, event_type=EventType.TRACK_EXITED, class_name=state.class_name,
                    bbox=state.last_bbox, confidence=0.0, frame_idx=current_frame_idx, crop=None,
                ))
                dead_ids.append(track_id)
        for tid in dead_ids:
            del self.tracks[tid]
        return events

    @staticmethod
    def _hash_crop(crop) -> Optional[imagehash.ImageHash]:
        if crop is None:
            return None
        try:
            return imagehash.phash(Image.fromarray(crop[:, :, ::-1]))
        except Exception:
            return None