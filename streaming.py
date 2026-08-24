import logging
import time
import uuid
from collections import deque
from typing import Callable

import m3u8
from locust.contrib.fasthttp import FastHttpSession, FastResponse

from player import ABRModel, Segment, BufferUnderrun


class HLSSession:
    def __init__(self,
                 playlist: str,
                 session: FastHttpSession,
                 sleep: Callable[[float], None],
                 userid: str = None,
                 ) -> None:
        self._playlist = playlist
        self._session = session
        self._sleep = sleep
        self._userid = userid if userid else str(uuid.uuid4())

        self._player = ABRModel(3)
        self._init_state()

    def _init_state(self) -> None:
        """Initialize mutable playback state"""
        self._last_playlist_fingerprint = ()
        self._fetch_playlist_at: float = -1
        self._last_media_sequence_seen: int = -1
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

    def _fetch_manifest(self) -> m3u8.M3U8:
        # fetch playlist
        with self._session.get(self._playlist, catch_response=True) as response:
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

    def _fetch_segment(self, uri: str) -> FastResponse:
        t0 = time.perf_counter()
        response = self._session.get(uri)
        response.raise_for_status()

        self._sleep(2.5)  # simulate some processing time for the segment, TODO: remove

        elapsed_s = max(time.perf_counter() - t0, 1e-6)
        size_bytes = len(response.content or b"")
        self._throughput_samples.append((size_bytes * 8) / elapsed_s)
        return response

    def step(self) -> bool:
        """Advance the session by one step. Return True if the session is still active, False if it has ended."""
        try:
            # initialize timing
            if self._fetch_playlist_at < 0:
                self._fetch_playlist_at = time.monotonic()

            # fetch playlist
            playlist = self._fetch_manifest()
            assert playlist.target_duration is not None, "Playlist must have target_duration"

            if self._last_playlist_fingerprint == self.playlist_fingerprint(playlist):
                # playlist has not changes, wait 0.5 target_duration
                self._fetch_playlist_at += playlist.target_duration / 2

            else:
                # first or changed playlist, wait 1 target_duration
                self._fetch_playlist_at += playlist.target_duration

                if self._last_playlist_fingerprint == ():
                    self._last_playlist_fingerprint = self.playlist_fingerprint(playlist)

                    # first playlist, start buffering
                    self._player.start_buffering(playlist.target_duration)

                    # fill buffer ASAP and start play
                    for media_segment in playlist.segments[-self._player.buffer_target_n:]:
                        # download segment
                        self._fetch_segment(media_segment.absolute_uri)

                        self._player.add_segment(Segment(media_segment.media_sequence, media_segment.duration))
                        self._last_media_sequence_seen = media_segment.media_sequence

                    # start play
                    self._player.start_playing()
                    self._session.user.environment.startup_time_event.fire(time=self._player.startup_time, userid=self.userid)

                else:
                    self._last_playlist_fingerprint = self.playlist_fingerprint(playlist)

                    # changed playlist, get next segment after the last seen one
                    media_segment = next((s for s in playlist.segments if s.media_sequence > self._last_media_sequence_seen), None)
                    if media_segment is None:
                        raise Exception(f"manifest changed, but no new segment seen?!")

                    self._fetch_segment(media_segment.absolute_uri)

                    self._player.add_segment(Segment(media_segment.media_sequence, media_segment.duration))
                    self._last_media_sequence_seen = media_segment.media_sequence

            # advance player
            self._player.advance()
            self._session.user.environment.buffer_level_event.fire(level=self._player.buffer_level[1], userid=self.userid)

            # wait
            self._sleep(max(0, self._fetch_playlist_at - time.monotonic()))

            logging.debug(f"{self.throughput_bps/1000/1000:.2f}Mbps, {self._player.buffer_level}")
            return True

        except BufferUnderrun as e:
            # we simulate a use-case, where clients are constantly trying to rebuffer, so we just reset the playback state and continue
            logging.warning(f"Buffer underrun {e}: rebuffering")
            self._session.user.environment.buffer_level_event.fire(level=-1, userid=self.userid)

            # reset playback to trigger rebuffering
            self._init_state()
            self._player.reset()

            # wait a bit before retrying
            self._sleep(1)
            return True
        except Exception as e:
            logging.exception(f"Error in HLS session for user {self.userid}: {e}")
            return False
