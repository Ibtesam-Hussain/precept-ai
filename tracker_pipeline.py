import os
import threading
import time
import cv2
from ultralytics import YOLO
from debounce import TrackStateManager
from event_queue import EventBridge
from track_events import EventType
import frame_bus
import runtime_stats
import thumbnails


class _FrameProfiler:
    _STAGES = (
        "next_result",
        "yolo_preprocess",
        "yolo_inference",
        "yolo_postprocess",
        "plot",
        "track_process",
        "crop_thumb",
        "jpeg_publish",
        "debounce_emit",
        "visualize",
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._camera_total = {"read": 0.0, "grab": 0.0, "retrieve": 0.0}
        self._camera_calls = {"read": 0, "grab": 0, "retrieve": 0}
        self.camera_fps = None
        self._samples = {stage: [] for stage in self._STAGES}
        self._frame_times = []
        self._warmup_until = time.monotonic() + 40
        self._measuring = False

    def record(self, stage, started):
        self.record_ms(stage, (time.perf_counter() - started) * 1000)

    def record_ms(self, stage, elapsed_ms):
        self._samples[stage].append(elapsed_ms)

    def record_camera_call(self, operation, started):
        with self._lock:
            self._camera_total[operation] += time.perf_counter() - started
            self._camera_calls[operation] += 1

    def camera_probe(self):
        original_capture = cv2.VideoCapture
        profiler = self

        class CaptureProbe:
            def __init__(self, *args, **kwargs):
                self._capture = original_capture(*args, **kwargs)
                profiler.camera_fps = self._capture.get(cv2.CAP_PROP_FPS)

            def __getattr__(self, name):
                return getattr(self._capture, name)

            def read(self, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return self._capture.read(*args, **kwargs)
                finally:
                    profiler.record_camera_call("read", started)

            def grab(self, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return self._capture.grab(*args, **kwargs)
                finally:
                    profiler.record_camera_call("grab", started)

            def retrieve(self, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return self._capture.retrieve(*args, **kwargs)
                finally:
                    profiler.record_camera_call("retrieve", started)

        class CameraProbeContext:
            def __enter__(self):
                cv2.VideoCapture = CaptureProbe

            def __exit__(self, exc_type, exc_value, traceback):
                cv2.VideoCapture = original_capture

        return CameraProbeContext()

    def report_frame(self, frame_w, frame_h, orig_w, orig_h, plot_w, plot_h, track_count):
        if not self._measuring:
            if time.monotonic() < self._warmup_until:
                return
            self._measuring = True
            self._samples = {stage: [] for stage in self._STAGES}
            with self._lock:
                self._camera_total = {"read": 0.0, "grab": 0.0, "retrieve": 0.0}
                self._camera_calls = {"read": 0, "grab": 0, "retrieve": 0}

        self._frame_times.append(time.perf_counter())
        if len(self._frame_times) < 50:
            return

        elapsed = self._frame_times[-1] - self._frame_times[0]
        fps = (len(self._frame_times) - 1) / elapsed if elapsed > 0 else 0
        stages = " ".join(
            f"{name}={sum(values) / len(values):.1f}/{max(values):.1f}ms"
            if values else f"{name}=0.0/0.0ms"
            for name, values in self._samples.items()
        )
        with self._lock:
            camera_means = {
                operation: (
                    self._camera_total[operation] / count * 1000 if count else 0
                )
                for operation, count in self._camera_calls.items()
            }
            camera_calls = self._camera_calls.copy()
            self._camera_total = {"read": 0.0, "grab": 0.0, "retrieve": 0.0}
            self._camera_calls = {"read": 0, "grab": 0, "retrieve": 0}

        print(
            f"[profile] fps={fps:.2f} cam_fps={self.camera_fps} "
            f"cam_read={camera_means['read']:.1f}ms({camera_calls['read']}) "
            f"grab={camera_means['grab']:.1f}ms({camera_calls['grab']}) "
            f"retrieve={camera_means['retrieve']:.1f}ms({camera_calls['retrieve']}) "
            f"published={frame_w}x{frame_h} orig={orig_w}x{orig_h} "
            f"plot={plot_w}x{plot_h} tracks={track_count} {stages}",
            flush=True,
        )
        self._samples = {stage: [] for stage in self._STAGES}
        self._frame_times.clear()


def run_tracker(
    source,
    bridge: EventBridge,
    model_path="yolo26n.pt",
    visualize=False,
    burn_boxes=False,
):
    profile = _FrameProfiler() if os.environ.get("PRECEPT_PROFILE") == "1" else None
    if profile is None:
        _run_tracker(source, bridge, model_path, visualize, burn_boxes, None)
    else:
        with profile.camera_probe():
            _run_tracker(source, bridge, model_path, visualize, burn_boxes, profile)


def _run_tracker(source, bridge, model_path, visualize, burn_boxes, profile):
    model = YOLO(model_path)
    state_mgr = TrackStateManager()
    frame_idx = 0
    first_seen_at = {}

    results = iter(model.track(source=source, stream=True, persist=True, verbose=False))
    while True:
        next_started = time.perf_counter() if profile else None
        try:
            result = next(results)
        except StopIteration:
            break
        if profile:
            profile.record("next_result", next_started)
            for stage, elapsed_ms in getattr(result, "speed", {}).items():
                stage_name = f"yolo_{stage}"
                if stage_name in profile._samples:
                    profile.record_ms(stage_name, elapsed_ms)

        frame_idx += 1
        runtime_stats.record_frame()

        plot_started = time.perf_counter() if profile else None
        annotated = (
            result.plot() if burn_boxes or visualize else result.orig_img
        )
        if profile:
            profile.record("plot", plot_started)
        frame = annotated if burn_boxes else result.orig_img
        frame_h, frame_w = frame.shape[:2]
        orig_h, orig_w = result.orig_img.shape[:2]
        plot_h, plot_w = annotated.shape[:2]

        encode_started = time.perf_counter() if profile else None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            frame_bus.set_frame(buf.tobytes())  # publish for the dashboard's live feed
        if profile:
            profile.record("jpeg_publish", encode_started)

        visualize_started = time.perf_counter() if profile and visualize else None
        if visualize:
            cv2.imshow("Tracker debug", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        if profile and visualize:
            profile.record("visualize", visualize_started)

        process_started = time.perf_counter() if profile else None
        crop_thumb_ms = 0.0
        debounce_emit_ms = 0.0
        track_count = 0
        if result.boxes is None or result.boxes.id is None:
            debounce_started = time.perf_counter() if profile else None
            for ev in state_mgr.sweep_exits(frame_idx):
                crop_thumb_ms += _publish_event(ev, bridge, profile)
                first_seen_at.pop(ev.track_id, None)
            if profile:
                debounce_emit_ms += (time.perf_counter() - debounce_started) * 1000
            thumbnails.set_active_track_ids(state_mgr.tracks)
            runtime_stats.publish_tracks(frame_idx, frame_w, frame_h, [])
            if profile:
                profile.record("track_process", process_started)
                profile.record_ms("crop_thumb", crop_thumb_ms)
                profile.record_ms("debounce_emit", debounce_emit_ms)
                profile.report_frame(
                    frame_w, frame_h, orig_w, orig_h, plot_w, plot_h, track_count
                )
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
            crop_started = time.perf_counter() if profile else None
            crop = _extract_crop(result.orig_img, (x1, y1, x2, y2))
            if profile:
                crop_thumb_ms += (time.perf_counter() - crop_started) * 1000

            debounce_started = time.perf_counter() if profile else None
            for ev in state_mgr.update(
                track_id=track_id, class_name=class_name, bbox=(x1, y1, x2, y2),
                confidence=float(conf), crop=crop, frame_idx=frame_idx,
            ):
                crop_thumb_ms += _publish_event(ev, bridge, profile)
            if profile:
                debounce_emit_ms += (time.perf_counter() - debounce_started) * 1000

        debounce_started = time.perf_counter() if profile else None
        for ev in state_mgr.sweep_exits(frame_idx):
            crop_thumb_ms += _publish_event(ev, bridge, profile)
            first_seen_at.pop(ev.track_id, None)
        if profile:
            debounce_emit_ms += (time.perf_counter() - debounce_started) * 1000
        thumbnails.set_active_track_ids(state_mgr.tracks)
        runtime_stats.publish_tracks(frame_idx, frame_w, frame_h, tracks)
        track_count = len(tracks)
        if profile:
            profile.record("track_process", process_started)
            profile.record_ms("crop_thumb", crop_thumb_ms)
            profile.record_ms("debounce_emit", debounce_emit_ms)
            profile.report_frame(
                frame_w, frame_h, orig_w, orig_h, plot_w, plot_h, track_count
            )


def _publish_event(event, bridge: EventBridge, profile=None):
    crop_started = time.perf_counter() if profile else None
    if event.event_type == EventType.TRACK_EXITED:
        thumbnails.clear_thumb(event.track_id)
    elif event.crop is not None:
        ok, buf = cv2.imencode(
            ".jpg", event.crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70]
        )
        if ok:
            thumbnails.set_thumb(event.track_id, buf.tobytes())
    crop_thumb_ms = (time.perf_counter() - crop_started) * 1000 if profile else 0

    bridge.put(event)
    return crop_thumb_ms


def _extract_crop(frame, bbox):
    x1, y1, x2, y2 = bbox
    crop = frame[max(0, y1):y2, max(0, x1):x2]
    return crop if crop.size else None