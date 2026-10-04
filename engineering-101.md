# Vision + LLM Object Tracking — Event-Driven Scaffold

## Flow

```
tracker_pipeline.py (sync, own thread)
    YOLO26.track() → per-frame detections
        → debounce.py (TrackStateManager) → filters to meaningful events
            → event_queue.py (EventBridge) → thread-safe handoff
                → llm_worker.py (async, main loop) → batches, caches,
                  calls cheap-tier LLM, escalates rarely, logs everything
```

## Why this shape

- The video loop (`tracker_pipeline.py`) **never awaits anything**. It pushes
  events onto a plain `queue.Queue` and moves on. Nothing about your video
  FPS depends on LLM latency.
- `debounce.py` is where most of your cost savings actually happen — most
  frames and most detections produce **zero** events. A track only ever
  generates: one NEW, one STABLE, occasional APPEARANCE_CHANGED, one EXITED.
- `llm_worker.py` batches multiple pending events into a single prompt/call,
  caches descriptions per track_id, and only escalates to a stronger model
  when a cheap heuristic says it's worth it.
- Every event is logged to `events_log.jsonl` regardless of whether the LLM
  was actually called — so you always have a full trail to replay/debug/
  fine-tune your triggers against later.
- LLM-generated descriptions are logged to `descriptions_log.jsonl` as a
  separate file, containing track metadata alongside the AI-generated text
  descriptions for analysis and debugging.

## Wiring in a real API

The system now includes `llm_request.py` with real OpenRouter API integration:

- `describe_batch_cheap()` — implements actual API calls to OpenRouter using
  the free Qwen 3.8-27B model. Processes batches of track events with image
  data encoded as base64 for vision-language model understanding.
- Set `OPENROUTER_API_KEY` environment variable to enable real API calls.
- The function handles multiple images in a single API call for efficiency,
  requesting one-sentence descriptions per image in order.

To use real API calls:
1. Set your OpenRouter API key: `export OPENROUTER_API_KEY=your_key_here`
2. The system will automatically use the live API instead of stub responses
3. Monitor costs on OpenRouter dashboard - the free tier model is used by default

## Tuning knobs (start here once it's running)

- `TrackStateManager(stable_frames=..., exit_after=..., appearance_hash_threshold=...)`
  in `debounce.py` — controls how "twitchy" event generation is. Raise
  `stable_frames` if flicker/false tracks are triggering events too early.
- `EventBridge.get_batch(max_batch=8, max_wait_s=1.0)` in `main.py`'s worker
  loop — controls batching aggressiveness vs. latency. Bigger batches =
  cheaper per-event cost, more delay before you get a description.
- `_should_escalate()` — your actual cost/quality lever. Tighten this once
  you see what the cheap tier's outputs look like in practice.

## Threshold Criteria — What, Why, and How We Set Them

### What These Thresholds Control

The `TrackStateManager` in `debounce.py` uses four key thresholds to determine when to generate events and trigger LLM calls:

1. **`stable_frames=10`**: How many consecutive frames a track must exist before being marked "stable" and triggering the first LLM description
2. **`exit_after=15`**: How many frames of absence before considering a track "exited" and cleaning it up
3. **`appearance_hash_threshold=30`**: Perceptual hash difference threshold (0-64) that determines if an object's appearance has changed enough to warrant a new LLM description
4. **`recheck_cooldown_frames=30`**: Minimum frames between appearance change checks (prevents spam during rapid movements)

### Why We Need These Thresholds

**Cost Optimization**: LLM API calls cost money. Without smart debouncing:
- 30 FPS video = 30 potential LLM calls per second per object
- 1 hour tracking = 108,000 potential LLM calls per object
- At $0.01 per call = $1,080 per hour per object (prohibitive!)

**False Positive Prevention**: Computer vision is noisy:
- Flickering detections create ghost tracks
- Lighting changes trigger false "appearance changes"
- Normal movements (head turns, sitting adjustments) shouldn't count as appearance changes

**Real-time Performance**: The video tracking loop must never block:
- LLM calls can take 1-5 seconds
- Video runs at 30 FPS (33ms per frame)
- Thresholds ensure we batch and debounce rather than calling per-frame

### How We Determined the Values

**Empirical Testing Process**:

1. **Started with `appearance_hash_threshold=14`** (common default)
   - **Problem**: Too sensitive — head turns and sitting movements triggered appearance changes
   - **Result**: Excessive LLM calls, wasted money

2. **Increased to `appearance_hash_threshold=22`**
   - **Problem**: Still too sensitive — sitting movements still triggered changes
   - **Result**: Better, but still too many false positives

3. **Increased to `appearance_hash_threshold=35`**
   - **Problem**: Too strict — might miss real appearance changes
   - **Result**: Zero false positives, but potentially missing real events

4. **Tested middle ground (`24` and `28`)**
   - **Result**: Both performed similarly — ignored subtle movements but caught real changes
   - **Conclusion**: Sweet spot found in 24-28 range

5. **Final choice: `appearance_hash_threshold=30`**
   - **Rationale**: Slightly more conservative than 24-28 to ensure cost savings while still catching significant appearance changes
   - **Balance**: Ignores normal movements (sitting, head turns) but catches real changes (clothing, objects, major pose changes)

**Why Other Thresholds Were Set This Way**:

- **`stable_frames=10`**: ~0.3 seconds at 30 FPS — long enough to filter momentary detections, short enough for responsive tracking
- **`exit_after=15`**: ~0.5 seconds absence — tracks don't disappear during brief occlusions but cleanup when genuinely gone
- **`recheck_cooldown_frames=30`**: ~1 second between checks — prevents rapid-fire checks during continuous movement

### Perceptual Hashing Explained

**What is it?**: A perceptual hash (pHash) creates a 64-bit "fingerprint" of an image that represents what it looks like rather than exact pixel values.

**How it works**:
- Similar images → similar hash codes (small Hamming distance)
- Different images → very different hash codes (large Hamming distance)
- Hamming distance = number of bits that differ (0-64 range)

**Why we use it**: It's robust to:
- Small position changes (object moving in frame)
- Lighting changes (shadows, brightness)
- Compression artifacts
- Minor pose changes

But still sensitive to:
- Clothing changes
- Picking up/holding objects
- Major pose changes
- Significant appearance alterations

### Threshold Ranges and Their Effects

**`appearance_hash_threshold` ranges**:
- **0-5**: Essentially identical (only compression differences)
- **5-10**: Very similar (same object, slightly different angle)
- **10-15**: Similar but noticeably different (original threshold 14 was too sensitive)
- **15-25**: Different enough to notice (our testing showed 24-28 worked well)
- **25-35**: Significant changes (final choice of 30 for cost savings)
- **35+**: Very different (might miss some real changes)

### Cost Impact Analysis

**Example: 1 hour of person tracking**

Without debouncing (theoretical worst case):
- 30 FPS × 3600 seconds = 108,000 frames
- 108,000 LLM calls × $0.01 = $1,080

With our thresholds (typical case):
- 1 track_stable event = 1 LLM call
- 2-3 track_appearance_changed events = 2-3 LLM calls
- Total: ~3-4 LLM calls × $0.01 = $0.03-0.04

**Savings: ~99.6% cost reduction**

### When to Adjust These Thresholds

**Increase `appearance_hash_threshold` if**:
- You're getting too many appearance change events from normal movements
- LLM costs are higher than expected
- You want to be more conservative about what counts as "appearance changed"

**Decrease `appearance_hash_threshold` if**:
- You're missing real appearance changes you want to capture
- Objects are changing appearance but not triggering events
- You need more sensitivity for your specific use case

**Increase `stable_frames` if**:
- You're getting too many track_new/track_stable events from flickering detections
- False tracks are being created and destroyed rapidly

**Decrease `stable_frames` if**:
- Tracking feels too slow to respond
- You need faster initial descriptions
- Your detection quality is very high and stable

## Next steps for the prototype

1. Run with a local video file first (`--source path/to/video.mp4`) —
   cheaper to iterate than a live webcam/RTSP stream.
2. Watch `events_log.jsonl` fill up before wiring the real LLM calls, to
   sanity-check your debounce thresholds against actual event volume.
3. Fill in `describe_batch_cheap` / `judge_escalation` with your provider
   of choice, start with a tiny `max_batch` to check the prompt format
   before scaling up.
4. Once stable, swap `yolo26n.pt` for `yolo26s.pt` only if you find recall
   is the bottleneck, not before.
