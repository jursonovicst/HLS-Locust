import time
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Segment:
    sequence_no: int
    duration: float


class BufferUnderrun(Exception):
    pass


class ABRModel:

    def __init__(self, buffer_target_n: int):
        self._buffer_target_n = buffer_target_n
        self._target_duration: float = -1
        self._segment_buffer: List[Segment] = []

        self._start_buffering_at: float = -1
        self._start_play_at: float = -1
        self._play_at: float = -1
        self._buffer_at: float = -1
        self._next_fetch_at: float = -1

    @property
    def buffer_target_n(self) -> int:
        return self._buffer_target_n

    @property
    def buffer_length(self) -> int:
        return len(self._segment_buffer)

    @property
    def is_initializing(self) -> bool:
        return 0 > self._start_buffering_at

    def start_buffering(self, target_duration: float) -> None:
        self._target_duration = target_duration
        self._start_buffering_at = time.monotonic()

    @property
    def is_buffering(self) -> bool:
        return self._start_buffering_at >= 0 > self._start_play_at

    def start_playing(self) -> None:
        self._start_play_at = time.monotonic()
        self._play_at = self._start_play_at
        self._buffer_at = self._start_play_at + sum([s.duration for s in self._segment_buffer])

    @property
    def is_playing(self) -> bool:
        return self._start_play_at >= 0

    def add_segment(self, segment: Segment) -> None:
        # segments are added in order
        self._segment_buffer.append(segment)
        self._buffer_at += segment.duration

    def advance(self) -> None:
        """Player control logic, return the time for the next segment."""
        if not self.is_playing:
            return

        # consume segments till we are up2date
        try:
            while self._play_at < time.monotonic() - self._target_duration:
                segment = self._segment_buffer.pop(0)
                self._play_at += segment.duration
        except IndexError as e:
            raise BufferUnderrun(e)

    @property
    def startup_time(self) -> float| None:
        if self._start_buffering_at < 0 or self._start_play_at < 0:
            return None
        return self._start_play_at - self._start_buffering_at

    @property
    def buffer_level(self) -> Tuple[int, float]:
        return len(self._segment_buffer), self._buffer_at - self._play_at
