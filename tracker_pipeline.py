import os
import cv2
from ultralytics import YOLO
from debounce import TrackStateManager
from event_queue import EventBridge
from track_events import EventType
import frame_bus
import thumbnails


def run_tracker(source, bridge: EventBridge, model_path="yolo26n.pt", visualize=False):
    model = YOLO(model_path)
    state_mgr = TrackStateManager()
    frame_idx = 0

    for result in model.track(source=source, stream=True, persist=True, verbose=False):
        frame_idx += 1

        annotated = result.plot()  # draws boxes + track IDs

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
            thumbnails.set_active_track_ids(state_mgr.tracks)
            continue

        boxes = result.boxes
        for box, track_id, cls, conf in zip(
            boxes.xyxy.cpu().numpy(), boxes.id.cpu().numpy(),
            boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy(),
        ):
            track_id = int(track_id)
            class_name = model.names[int(cls)]
            x1, y1, x2, y2 = map(int, box)
            crop = _extract_crop(result.orig_img, (x1, y1, x2, y2))

            for ev in state_mgr.update(
                track_id=track_id, class_name=class_name, bbox=(x1, y1, x2, y2),
                confidence=float(conf), crop=crop, frame_idx=frame_idx,
            ):
                _publish_event(ev, bridge)

        for ev in state_mgr.sweep_exits(frame_idx):
            _publish_event(ev, bridge)
        thumbnails.set_active_track_ids(state_mgr.tracks)


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