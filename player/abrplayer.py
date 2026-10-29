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
        :param player_id: A unique identifier for the client. This is used to distinguish between different clients in the simulation.
        :param clock: A callable that returns the current time in seconds. If None, time.monotonic will be used.

        Player state model is: initializing -> buffering -> playing. The player can be in one of these states at any given time.

            bs | ps
        ini N    N
        buf Y    N
        pla Y    Y

        """
        self._clock_ts = clock if clock is not None else time.monotonic

        self._buffering_started_ts: Optional[float] = None  # time when buffering began; None means not started
        self._play_started_ts: Optional[float] = None  # time when playback began; None means not started
        self._play_started_wc: Optional[float] = None  # time when playback began (wall clock) Used to convert at time to wall clock time
        self._buffer_end_ts: float = 0.0  # time when the playback will fail, because the segments in the buffer cannot cover playback; None means not started
        self._next_fetch_ts: Optional[float] = None  # scheduled time for next fetch; None if none

    @property
    def is_initializing(self) -> bool:
        """
        :return: True if the player is initializing, False otherwise.
        """
        return self._buffering_started_ts is None

    def start_buffering(self) -> None:
        """
        Start buffering with the specified target duration.
        :return: None
        """
        assert self.is_initializing, "Player must be initializing to start buffering"
        self._buffering_started_ts = self._clock_ts()

    @property
    def is_buffering(self) -> bool:
        return self._buffering_started_ts is not None and self._play_started_ts is None

    def start_playing(self) -> Tuple[float, float]:
        """
        Start playing the buffered segments. Use the buffer() and advance() methods to manage the buffer and play.
        :return: The duration it took to start playback and time wall clock time it happened.
        """
        assert self.is_buffering, "Player must be buffering to start playing"

        self._play_started_ts = self._clock_ts()
        self._play_started_wc = time.time()  # Record the wall clock time
        self._buffer_end_ts += self._play_started_ts  # convert the buffered duration to an absolute monotonic time

        return self._play_started_ts - self._buffering_started_ts, self._play_started_wc

    @property
    def is_playing(self) -> bool:
        """
        :return: True if the player is playing, False otherwise.
        """
        return self._play_started_ts is not None

    def buffer(self, segment: Segment) -> None:
        """
        Simulate to add a segment to the buffer.
        :param segment: The segment to add.
        :return: None
        """
        assert self.is_buffering or self.is_playing, "Player must be buffering or playing to buffer."

        # accumulate buffered duration; start_playing will convert this to an absolute monotonic time
        self._buffer_end_ts += segment.duration

    def _ts_to_wc(self, ts: float) -> float:
        """
        Convert a monotonic timestamp to a wall clock timestamp.
        :param ts: The monotonic timestamp to convert.
        :return: The wall clock timestamp.
        """
        assert self._play_started_ts is not None and self._play_started_wc is not None, "Player must be playing to convert timestamps."
        return self._play_started_wc + ts - self._play_started_ts

    def advance(self) -> None:
        """
        Player control logic.
        :return: The current duration of buffered media in seconds.
        """
        assert self.is_playing, "Player must be playing to advance."

        # if play is ahead of the buffer, we have a buffer underrun
        play_at = self._clock_ts()
        if play_at > self._buffer_end_ts:
            # convert the buffered duration to an absolute wall clock time for the error
            raise BufferUnderrunError(f"Buffer underrun, played {self._play_started_ts - self._buffer_end_ts:.2f}s", time_wc=self._ts_to_wc(self._buffer_end_ts))

    @property
    def buffer_level(self) -> float | None:
        """
        :return: The duration of buffered media in seconds, or None if it is empty.
        """
        assert self.is_playing, "Player must be playing"

        level = self._buffer_end_ts - self._clock_ts()
        return level if level > 0 else None
