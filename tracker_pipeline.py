import time
import cv2
from ultralytics import YOLO
from debounce import TrackStateManager
from event_queue import EventBridge
from track_events import EventType
import frame_bus
import runtime_stats
import thumbnails


def run_tracker(source, bridge: EventBridge, model_path="yolo26n.pt", visualize=False):
    model = YOLO(model_path)
    state_mgr = TrackStateManager()
    frame_idx = 0
    first_seen_at = {}

    for result in model.track(source=source, stream=True, persist=True, verbose=False):
        frame_idx += 1
        runtime_stats.record_frame()

        annotated = result.plot()  # draws boxes + track IDs
        frame_h, frame_w = annotated.shape[:2]

        ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            frame_bus.set_frame(buf.tobytes())  # publish for the dashboard's live feed

        if visualize:
            cv2.imshow("Tracker debug", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        if result.boxes is None or result.boxes.id is None:
            for ev in state_mgr.sweep_exits(frame_idx):
                _publish_event(ev, bridge)
                first_seen_at.pop(ev.track_id, None)
            thumbnails.set_active_track_ids(state_mgr.tracks)
            runtime_stats.publish_tracks(frame_idx, frame_w, frame_h, [])
            continue

        boxes = result.boxes
        tracks = []
        now = time.monotonic()
        for box, track_id, cls, conf in zip(
            boxes.xyxy.cpu().numpy(), boxes.id.cpu().numpy(),
            boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy(),
        ):
            track_id = int(track_id)
            first_seen_at.setdefault(track_id, now)
            class_name = model.names[int(cls)]
            x1, y1, x2, y2 = map(int, box)
            left = min(max(x1, 0), frame_w)
            top = min(max(y1, 0), frame_h)
            right = min(max(x2, 0), frame_w)
            bottom = min(max(y2, 0), frame_h)
            if right > left and bottom > top and frame_w > 0 and frame_h > 0:
                tracks.append({
                    "track_id": track_id,
                    "class_name": class_name,
                    "conf": round(float(conf), 2),
                    "box": [
                        left / frame_w,
                        top / frame_h,
                        right / frame_w,
                        bottom / frame_h,
                    ],
                    "age_s": round(now - first_seen_at[track_id], 2),
                })
            crop = _extract_crop(result.orig_img, (x1, y1, x2, y2))

            for ev in state_mgr.update(
                track_id=track_id, class_name=class_name, bbox=(x1, y1, x2, y2),
                confidence=float(conf), crop=crop, frame_idx=frame_idx,
            ):
                _publish_event(ev, bridge)

        for ev in state_mgr.sweep_exits(frame_idx):
            _publish_event(ev, bridge)
            first_seen_at.pop(ev.track_id, None)
        thumbnails.set_active_track_ids(state_mgr.tracks)
        runtime_stats.publish_tracks(frame_idx, frame_w, frame_h, tracks)


def _publish_event(event, bridge: EventBridge) -> None:
    if event.event_type == EventType.TRACK_EXITED:
        thumbnails.clear_thumb(event.track_id)
    elif event.crop is not None:
        ok, buf = cv2.imencode(
            ".jpg", event.crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70]
        )
        if ok:
            thumbnails.set_thumb(event.track_id, buf.tobytes())

    bridge.put(event)


def _extract_crop(frame, bbox):
    x1, y1, x2, y2 = bbox
    crop = frame[max(0, y1):y2, max(0, x1):x2]
    return crop if crop.size else None