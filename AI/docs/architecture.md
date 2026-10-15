# Architecture and research contract

## Scope

The MVP is a causal, camera-only fall detector that can be trained and evaluated
on public data, run on a video or webcam, verify post-fall inactivity/recovery,
persist events, expose an API, and render a local dashboard.

URFD is the baseline dataset. Its official frame feature files define:

- `-1`: person is not lying;
- `0`: transition while falling;
- `1`: person is lying on the ground.

Those values are posture labels, not fall-event labels, because ADL sequences
also contain transitions and deliberate lying. They map to `UPRIGHT`,
`TRANSITION`, and `LYING`. A separate event target is derived for fall sequences
from the first sustained transition through impact/short post-impact hold; all
ADL windows remain event negatives. The derivation is recorded as
`urfd_depth_feature_derived`, not presented as manual ground truth. Eight-class
activity recognition is an extension that requires a dataset with the
corresponding activity annotations, such as UP-Fall.

## Data contract

Each extracted pose sequence is an `.npz` containing:

- `landmarks`: `float32[T, 33, 4]` (`x`, `y`, `z`, visibility);
- `valid`: `bool[T, 33]`;
- `timestamps_ms`: `int64[T]`;
- `frame_indices`: `int64[T]`;
- `posture_labels`: `int64[T]`;
- `event_labels`: `int64[T]`;
- `annotation_provenance`: `urfd_depth_feature_derived`.

`data/manifests/urfd_preview_sequences.csv` groups all frames from a sequence in one split.
URFD evaluation is therefore reported as **cross-sequence**, not cross-subject.

## Runtime interfaces

- `PoseEstimator.process(frame_bgr, frame_index, timestamp_ms) -> PoseFrame | None`
- `TemporalClassifier.predict(window) -> Prediction` (posture + fall probability)
- `FallStateMachine.update(prediction, pose) -> EventUpdate`

Windows are causal: 40 frames at 20 FPS, stride 5, with the target assigned to
the last frame. Landmarks are hip-centered and torso-scaled. Short missing gaps
may be interpolated; a validity mask is retained.

## State flow

```text
NORMAL → POSSIBLE_FALL → VERIFYING → CONFIRMED_FALL
                       ↘ RECOVERED
```

Probabilities are smoothed and duration thresholds use timestamps rather than
frame counts. Confirmed events are deduplicated during a cooldown period.
Recovery remains an explicit heuristic (upright pose after a fall), not a
separately trained claim.

## Acceptance criteria

1. A fresh Windows/Python environment can install the package and run `fallguard doctor`.
2. URFD downloads are resumable, license-gated, and safely extracted.
3. Preparation is restartable and emits validated pose `.npz` files.
4. No sequence/group appears in more than one split.
5. A CPU one-epoch smoke run emits a checkpoint, metadata, and metrics.
6. Evaluation reports class metrics plus event recall, false alarms/video, and delay.
7. Video inference emits annotated media and an event JSON record.
8. FastAPI validates skeleton/video input, persists events, and serves the dashboard.
9. Offline unit/integration tests pass without downloading public data.

Research targets such as event recall or false alarms are reported only after
the full real-data experiment has actually run.
