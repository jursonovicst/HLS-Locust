import logging
import time
import uuid
from collections import deque
from typing import Callable, Optional

import m3u8
from locust.contrib.fasthttp import FastHttpSession, LocustBadStatusCode
from locust.event import EventHook

from player import ABRPlayer, Segment, BufferUnderrunError


class HLSSession:
    def __init__(self,
                 playlist: str,
                 session: FastHttpSession,
                 sleep: Callable[[float | int], None],
                 events: tuple[EventHook, EventHook],
                 userid: Optional[str] = None,
                 ) -> None:
        self._playlist = playlist
        self._session = session
        self._sleep = sleep
        self._buffer_underrun_event, self._startup_time_event = events
        self._userid = userid if userid else str(uuid.uuid4())

        self._player = None
        self._init_state()

    def _init_state(self) -> None:
        """Initialize mutable playback state"""
        self._last_playlist_fingerprint = ()
        self._fetch_playlist_at: Optional[float] = None
        self._last_media_sequence_seen: Optional[int] = None
        self._throughput_samples = deque(maxlen=3)

    @classmethod
    def playlist_fingerprint(cls, playlist: m3u8.M3U8) -> tuple:
        last = playlist.segments[-1]
        return (
            playlist.media_sequence,
            len(playlist.segments),
            last.uri or last.absolute_uri,
            float(last.duration or 0)
        )

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
            # initialize timing
            if self._fetch_playlist_at is None:
                self._fetch_playlist_at = time.monotonic()

            # fetch playlist
            playlist = self._fetch_manifest()

            if self._last_playlist_fingerprint == self.playlist_fingerprint(playlist):
                # playlist has not changes, wait 0.5 target_duration
                self._fetch_playlist_at += playlist.target_duration / 2

            else:
                # first or changed playlist, wait 1 target_duration
                self._fetch_playlist_at += playlist.target_duration

                if self._last_playlist_fingerprint == ():
                    # init fingerprint
                    self._last_playlist_fingerprint = self.playlist_fingerprint(playlist)

                    # first playlist, create player
                    if playlist.target_duration is None:
                        raise ValueError("Playlist must have target_duration")
                    self._player = ABRPlayer(playlist.target_duration)

                    # fill buffer ASAP
                    self._player.start_buffering()
                    for media_segment in playlist.segments[-3:]:  # TODO: clarify buffer target (3)
                        # download segment (no retry during buffering)
                        self._fetch_segment(media_segment.absolute_uri)

                        self._player.buffer(Segment(media_segment.media_sequence, media_segment.duration))
                        self._last_media_sequence_seen = media_segment.media_sequence

                    # start play
                    startup_time = self._player.start_playing()
                    self._startup_time_event.fire(time=startup_time, userid=self.userid)

                else:
                    # update fingerprint
                    self._last_playlist_fingerprint = self.playlist_fingerprint(playlist)

                    # changed playlist, get next segment after the last seen one
                    assert self._last_media_sequence_seen is not None, "last_media_sequence_seen must be set after first playlist"
                    media_segment = next((s for s in playlist.segments if s.media_sequence > self._last_media_sequence_seen), None)
                    if media_segment is None:
                        raise Exception(f"manifest changed, but no new segment seen?!")

                    # fetch segment with retry
                    wait = 0.100
                    while True:
                        try:
                            buffer_level = self._player.advance()
                            self._fetch_segment(media_segment.absolute_uri)
                            break
                        except LocustBadStatusCode as e:
                            if e.code < 500 and e.code != 404:
                                # client error (except fragment not yet published), give up
                                raise

                            # server error, retry with exponential backoff
                            self._sleep(wait)
                            wait = min(wait * 2, buffer_level)  # no point to wait more than buffer level, because we will underrun anyway

                    # buffer segment
                    self._player.buffer(Segment(media_segment.media_sequence, media_segment.duration))
                    self._last_media_sequence_seen = media_segment.media_sequence

            # advance player
            self._player.advance()

            # wait
            self._sleep(max(0, self._fetch_playlist_at - time.monotonic()))

            return True

        except BufferUnderrunError as e:
            # we simulate a use-case, where clients are constantly trying to rebuffer, so we just reset the playback state and continue
            logging.warning(f"{e}: rebuffering")
            self._buffer_underrun_event.fire(userid=self.userid)

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
