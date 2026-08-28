import collections
import time
from dataclasses import dataclass
from typing import Tuple, Optional, Callable

from .errors import BufferUnderrunError


@dataclass(frozen=True)
class Segment:
    sequence_no: int
    duration: float


class ABRPlayer:
    """
    Adaptive Bitrate Player Model for HLS playback.

    This class should be Locust independent, and should not depend on any Locust specific code. It is a simple model of a player that can buffer and play segments, and can be used to simulate the
    behavior of a real player.
    """

    def __init__(self, buffer_target_n: int, target_duration: float, clock: Callable[[None], float] | None = None) -> None:
        """
        Initialize the ABRModel.
        :param buffer_target_n: The number of segments to buffer before starting playback. The player will try to maintain this number of segments in the buffer while playing.
        :param target_duration: The target duration for buffering.
        :param clock: A callable that returns the current time in seconds. If None, time.monotonic will be used.
        """
        if buffer_target_n <= 0:
            raise ValueError("buffer_target_n must be > 0")
        self._buffer_target_n = buffer_target_n
        if target_duration <= 0:
            raise ValueError("target_duration must be > 0")
        self._target_duration = target_duration
        self._clock = clock if clock is not None else time.monotonic

        self._segment_buffer: collections.deque[Segment] = collections.deque()  # TODO: implement maxlen=buffer_target_n to watch for buffer underrun

        self._start_buffering_at: Optional[float] = None  # monotonic time when buffering began; None means not started
        self._start_play_at: Optional[float] = None  # monotonic time when playback began; None means not started
        self._play_at: Optional[float] = None  # monotonic playhead time (time progressed during playback)
        self._buffer_at: float = 0.0  # monotonic time when the last segment in the buffer will be played; None means not started
        self._next_fetch_at: Optional[float] = None  # scheduled monotonic time for next fetch; None if none

    @property
    def buffer_target_n(self) -> int:
        """
        :return: number of segments to buffer before starting playback. The player will try to maintain this number of segments in the buffer while playing.
        """
        return self._buffer_target_n

    @property
    def buffer_length_n(self) -> int:
        """
        :return: number of segments currently buffered.
        """
        return len(self._segment_buffer)

    @property
    def is_initializing(self) -> bool:
        """
        :return: True if the player is initializing, False otherwise.
        """
        return self._start_buffering_at is None

    def start_buffering(self) -> None:
        """
        Start buffering with the specified target duration.
        :return: None
        """
        self._start_buffering_at = self._clock()

    @property
    def is_buffering(self) -> bool:
        return self._start_buffering_at is not None and self._start_play_at is None

    def start_playing(self) -> float:
        """
        Start playing the buffered segments. This will set the playhead to the current time. Use the buffer() and advance() methods to manage the buffer and playhead.
        :return: The time it took to start playback, or None if playback has not started yet.
        """
        assert self._start_buffering_at is not None, "Player must be buffering to start playing"

        self._start_play_at = self._clock()
        self._play_at = self._start_play_at
        self._buffer_at += self._start_play_at  # convert the buffered duration to an absolute monotonic time

        return self._start_play_at - self._start_buffering_at

    @property
    def is_playing(self) -> bool:
        return self._start_play_at is not None

    def buffer(self, segment: Segment) -> None:
        """
        Add a segment to the buffer.
        :param segment: The segment to add.
        :return: None
        """
        # segments are added in order
        self._segment_buffer.append(segment)
        # accumulate buffered duration; start_playing will convert this to an absolute monotonic time
        self._buffer_at += segment.duration

    def advance(self) -> Tuple[int, float]:
        """
        Player control logic, advance the playhead.
        :return: A tuple of (number of segments in buffer, duration of buffered media in seconds). The duration is the time from the playhead to the end of the last buffered segment.
        """
        assert self._play_at is not None, "Player must be playing to advance"

        # consume segments till play_at is within the first segment in the buffer
        while self._clock() > self._play_at:
            try:
                # the playhead is behind now, consume the first segment from the buffer
                segment = self._segment_buffer.popleft()
            except IndexError as e:
                # this may happen in the past, use self._play_at as the time of the error
                raise BufferUnderrunError(self._play_at, e)
            # advance the playhead by the duration of the segment
            self._play_at += segment.duration

        assert self._buffer_at is not None, "buffer_at must be set if playing"
        return len(self._segment_buffer), self._buffer_at - self._play_at
