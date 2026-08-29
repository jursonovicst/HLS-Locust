import time
from dataclasses import dataclass
from typing import Optional, Callable, Tuple

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

    def __init__(self, clock: Callable[[None], float] | None = None) -> None:
        """
        Initialize the ABRModel.
        :param clock: A callable that returns the current time in seconds. If None, time.monotonic will be used.
        """
        self._clock = clock if clock is not None else time.monotonic

        self._start_buffering_at: Optional[float] = None  # time when buffering began; None means not started
        self._start_play_at: Optional[float] = None  # time when playback began; None means not started
        self._start_play_at_wall_clock: Optional[
            float] = None  # time when playback began (wall clock) Used to convert at time to wall clock time
        self._buffer_at: float = 0.0  # time when the playback will fail, because the segments in the buffer cannot cover playback; None means not started
        self._next_fetch_at: Optional[float] = None  # scheduled time for next fetch; None if none

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
        assert self._start_buffering_at is None, "Player must be initializing to start buffering"
        self._start_buffering_at = self._clock()

    @property
    def is_buffering(self) -> bool:
        return self._start_buffering_at is not None and self._start_play_at is None

    def start_playing(self) -> Tuple[float, float]:
        """
        Start playing the buffered segments. Use the buffer() and advance() methods to manage the buffer and play.
        :return: The duration it took to start playback and time wall clock time it happened.
        """
        assert self._start_buffering_at is not None and self._start_play_at is None, "Player must be buffering to start playing"

        self._start_play_at = self._clock()
        self._start_play_at_wall_clock = time.time()  # Record the wall clock time
        self._buffer_at += self._start_play_at  # convert the buffered duration to an absolute monotonic time

        return self._start_play_at - self._start_buffering_at, self._start_play_at_wall_clock

    @property
    def is_playing(self) -> bool:
        return self._start_play_at is not None

    def buffer(self, segment: Segment) -> None:
        """
        Simulate to add a segment to the buffer.
        :param segment: The segment to add.
        :return: None
        """
        # accumulate buffered duration; start_playing will convert this to an absolute monotonic time
        self._buffer_at += segment.duration

    def advance(self) -> float:
        """
        Player control logic.
        :return: The current duration of buffered media in seconds.
        """
        assert self._start_play_at is not None, "Player must be playing to advance."

        # if play is ahead of the buffer, we have a buffer underrun
        play_at = self._clock()
        if play_at > self._buffer_at:
            # convert the buffered duration to an absolute wall clock time for the error
            raise BufferUnderrunError(self._start_play_at_wall_clock + self._buffer_at - self._start_play_at,
                                      f"Buffer underrun at {self._buffer_at:.2f}s, play at {play_at:.2f}s")

        return self._buffer_at - play_at

    @property
    def buffer_level(self) -> float | None:
        """
        :return: The duration of buffered media in seconds, or None if it is empty.
        """
        assert self._start_play_at is not None, "Player must be playing"
        level = self._buffer_at - self._clock()
        return level if level > 0 else None
