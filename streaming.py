import logging
import time
import uuid
from collections import deque
from typing import Callable, Optional

import m3u8
from locust.contrib.fasthttp import FastHttpSession, LocustBadStatusCode
from locust.env import Environment
from locust.event import EventHook
from locust.exception import CatchResponseError

from player import ABRPlayer, Segment, BufferUnderrunError


class HLSSession:
    buffer_underrun_event = EventHook()
    startup_time_event = EventHook()

    @classmethod
    def listen(cls, event: EventHook):
        def deco(fn):
            event.add_listener(fn)
            return fn

        return deco

    def __init__(self,
                 playlist: str,
                 session: FastHttpSession,
                 environment: Environment,
                 sleep: Callable[[float | int], None],
                 userid: Optional[str] = None,
                 ) -> None:
        self._playlist = playlist
        self._session = session
        self._sleep = sleep
        self._environment = environment
        self._userid = userid if userid else str(uuid.uuid4())

        self._player = None
        self._init_state()

    def _init_state(self) -> None:
        """Initialize mutable playback state"""
        self._fetch_playlist_at: Optional[float] = None
        self._last_media_sequence_seen: Optional[
            int] = None  # make it negative to let always capture the first segment whatever its media_sequence is
        self._throughput_samples = deque(maxlen=3)

    @property
    def userid(self) -> str:
        return self._userid

    @property
    def throughput_bps(self) -> float:
        if not self._throughput_samples:
            return 0.0
        return sum(self._throughput_samples) / len(self._throughput_samples)

    def _fetch_manifest(self, name="manifest") -> m3u8.M3U8:
        # fetch playlist
        with self._session.get(self._playlist, catch_response=True, name=name) as response:
            if response.status_code == 0:
                response.failure(f"Failed to connect to the server: {response.error}")
                response.raise_for_status()

            if response.status_code >= 400:
                response.failure(f"Failed to fetch playlist: {response.status_code}")
                response.raise_for_status()

            # parse the playlist
            try:
                playlist = m3u8.loads(response.text, uri=self._playlist)
            except ValueError as e:
                response.failure(e)
                raise Exception(e)

            response.success()

        if playlist.is_variant:
            # select the first variant stream, and fetch that variance
            self._playlist = playlist.playlists[0].absolute_uri
            return self._fetch_manifest()
        else:
            return playlist

    def _fetch_segment(self, uri: str, name="segment") -> None:
        t0 = time.perf_counter()
        response = self._session.get(uri, name=name)
        response.raise_for_status()

        elapsed_s = max(time.perf_counter() - t0, 1e-6)
        size_bytes = len(response.content or b"")
        self._throughput_samples.append((size_bytes * 8) / elapsed_s)

    def step(self) -> bool:
        """
        Advance the session by one step. Return True if the session is still active, False if it has ended.
        :return: True if the session is still active, False if it has ended.
        """
        try:
            # fetch playlist
            playlist = self._fetch_manifest()

            # first run?
            if self._last_media_sequence_seen is None:
                # create player
                self._player = ABRPlayer()

                # fill buffer ASAP
                self._player.start_buffering()
                for media_segment in playlist.segments[-3:]:  # TODO: clarify buffer target (3)
                    # download segment (no retry during buffering)
                    self._fetch_segment(media_segment.absolute_uri)
                    if media_segment.media_sequence is None or media_segment.duration is None:
                        raise Exception(f"Invalid media segment: {media_segment}")

                    self._player.buffer(Segment(media_segment.media_sequence, media_segment.duration))
                    self._last_media_sequence_seen = media_segment.media_sequence

                # initialize next fetch timestamp
                self._fetch_playlist_at = time.monotonic() + playlist.target_duration

                # start play
                startup_duration, startup_time_wall_clock = self._player.start_playing()
                self.startup_time_event.fire(environment=self._environment, duration=startup_duration,
                                             userid=self.userid, time=startup_time_wall_clock)

            else:
                # identify next segment (according to rfc8216 6.3.5)
                while (media_segment := next(
                        (s for s in playlist.segments if s.media_sequence > self._last_media_sequence_seen),
                        None)) is None:
                    if playlist.is_endlist:
                        return False  # no future segment will appear, end user session

                    # just stale playlist, retry
                    self._sleep(playlist.target_duration / 2)
                    self._player.advance()
                    playlist = self._fetch_manifest()
                    self._player.advance()

                if media_segment.media_sequence is None or media_segment.duration is None:
                    raise Exception(f"Invalid media segment: {media_segment}")

                # fetch that segment with retry
                wait = 0.100
                while True:  # if consequent segment fetches are unsuccessful, we leave this loop with a buffer underrun
                    try:
                        buffer_level = self._player.advance()
                        self._fetch_segment(media_segment.absolute_uri)
                        self._last_media_sequence_seen = media_segment.media_sequence
                        break
                    except LocustBadStatusCode as e:
                        if e.code < 500 and e.code != 404:
                            # client error (except fragment not yet published), give up
                            raise

                        # server error, retry with exponential backoff
                        self._sleep(wait)
                        wait = min(wait * 2, buffer_level)  # no point to wait more than buffer level, underrun anyway

                # buffer segment
                self._player.buffer(Segment(media_segment.media_sequence, media_segment.duration))

                # set next fetch time
                self._fetch_playlist_at += playlist.target_duration

            # advance player
            self._player.advance()

            # wait till next fetch
            self._sleep(max(0, self._fetch_playlist_at - time.monotonic()))

            return True

        except (BufferUnderrunError, CatchResponseError) as e:
            # we simulate a use-case, where clients are constantly trying to rebuffer, so we just reset the playback state and continue
            logging.warning(f"{e.__class__.__name__}: {e}, rebuffering")

            if isinstance(e, BufferUnderrunError):
                self.buffer_underrun_event.fire(environment=self._environment, userid=self.userid, time=e.at_wall_clock)

            # reset playback to trigger rebuffering
            self._init_state()
            del self._player
            self._player = None

            # wait a bit before retrying
            self._sleep(1)
            return True
        except Exception as e:
            logging.exception(f"Error in HLS session for user {self.userid}: {e}")
            return False
