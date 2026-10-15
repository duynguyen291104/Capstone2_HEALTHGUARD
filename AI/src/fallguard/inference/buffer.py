from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Sequence

from fallguard.domain import PoseFrame


class CausalSequenceBuffer(Sequence[PoseFrame]):
    """A fixed-size, timestamp-ordered window with stride-aware readiness.

    The window never contains a future frame relative to its target: callers
    always receive the most recent ``window_size`` observations and should
    assign a prediction to the final frame.
    """

    def __init__(self, window_size: int = 40, stride: int = 5) -> None:
        if window_size < 1:
            raise ValueError("window_size must be at least 1")
        if stride < 1:
            raise ValueError("stride must be at least 1")
        self.window_size = int(window_size)
        self.stride = int(stride)
        self._frames: deque[PoseFrame] = deque(maxlen=self.window_size)
        self._frames_seen = 0
        self._last_timestamp_ms: int | None = None
        self._last_frame_index: int | None = None

    def append(self, frame: PoseFrame) -> bool:
        """Append a frame and return whether a prediction is due."""

        if self._last_timestamp_ms is not None and frame.timestamp_ms < self._last_timestamp_ms:
            raise ValueError("PoseFrame timestamps must be monotonically non-decreasing")
        if self._last_frame_index is not None and frame.frame_index <= self._last_frame_index:
            raise ValueError("PoseFrame frame_index values must be strictly increasing")
        self._frames.append(frame)
        self._frames_seen += 1
        self._last_timestamp_ms = int(frame.timestamp_ms)
        self._last_frame_index = int(frame.frame_index)
        return self.ready

    push = append
    add = append

    @property
    def ready(self) -> bool:
        if len(self._frames) < self.window_size:
            return False
        return (self._frames_seen - self.window_size) % self.stride == 0

    @property
    def full(self) -> bool:
        return len(self._frames) == self.window_size

    @property
    def frames_seen(self) -> int:
        return self._frames_seen

    @property
    def target_timestamp_ms(self) -> int | None:
        return self._last_timestamp_ms

    def window(self) -> tuple[PoseFrame, ...]:
        if not self.full:
            raise RuntimeError(
                f"Sequence window is incomplete: {len(self._frames)}/{self.window_size} frames"
            )
        return tuple(self._frames)

    snapshot = window
    get_window = window

    @property
    def is_ready(self) -> bool:
        return self.ready

    def available(self) -> tuple[PoseFrame, ...]:
        """Return all currently buffered frames, even before the window is full."""

        return tuple(self._frames)

    def clear(self) -> None:
        self._frames.clear()
        self._frames_seen = 0
        self._last_timestamp_ms = None
        self._last_frame_index = None

    reset = clear

    def __len__(self) -> int:
        return len(self._frames)

    def __getitem__(self, index: int | slice) -> PoseFrame | tuple[PoseFrame, ...]:
        values = tuple(self._frames)
        return values[index]

    def __iter__(self) -> Iterator[PoseFrame]:
        return iter(tuple(self._frames))


SequenceBuffer = CausalSequenceBuffer
