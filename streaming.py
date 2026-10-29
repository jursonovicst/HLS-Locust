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

from player import ABRPlayer, Segment, PlayerError


class ManifestError(ValueError):
    pass


class HLSSession:
    startup_time_event = EventHook()

    player_error_event = EventHook()
    manifest_error_event = EventHook()
    http_error_event = EventHook()

    @classmethod
    def listen(cls, event: EventHook):
        def deco(fn):
            event.add_listener(fn)
            return fn

        return deco

    def __init__(self,
                 playlist_url: str,
                 profile_selector: Callable[..., m3u8.Playlist],
                 session: FastHttpSession,
                 environment: Environment,
                 sleep: Callable[[float | int], object],
                 user_id: Optional[str] = None,
                 ) -> None:
        self._playlist_url = playlist_url
        self._profile_selector = profile_selector
        self._session = session
        self._sleep = sleep
        self._environment = environment
        self._user_id = user_id or uuid.uuid4().hex

        self._player = None
        self._init_state()

    def _init_state(self) -> None:
        """Initialize mutable playback state"""
        self._master_playlist: Optional[m3u8.M3U8] = None
        self._fetch_playlist_at: Optional[float] = None
        self._last_media_sequence_seen: Optional[int] = None  # make it negative to let always capture the first segment whatever its media_sequence is
        self._throughput_samples = deque(maxlen=3)

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def throughput_bps(self) -> float:
        if not self._throughput_samples:
            return 0.0
        return sum(self._throughput_samples) / len(self._throughput_samples)

    def _fetch_manifest(self, url: str) -> m3u8.M3U8:
        """
        Fetch a manifest and parse it as M3U8. An invalid manifest will increase the failure count of the request.
        """
        with self._session.get(url, catch_response=True, name="manifest") as response:
            response.raise_for_status()
            try:
                content = response.text

                if not content.lstrip("\ufeff").startswith("#EXTM3U"):
                    raise ManifestError("Missing #EXTM3U header")

                playlist = m3u8.loads(content, uri=url)

                if not playlist.is_variant:
                    if not playlist.segments:
                        raise ManifestError("Empty media playlist")

                    if not playlist.target_duration or playlist.target_duration <= 0:
                        raise ManifestError("Invalid target duration")

                return playlist
            except ManifestError as e:
                response.failure(f"Invalid manifest: {e}")
                raise

    def _fetch_segment(self, uri: str, name="segment") -> None:
        ts_start = time.perf_counter()
        response = self._session.get(uri, name=name)
        elapsed_s = max(time.perf_counter() - ts_start, 1e-6)

        response.raise_for_status()

        # measure throughput only for successful requests, otherwise we would skew the average with failed requests
        self._throughput_samples.append(len(response.content or b"") * 8 / elapsed_s)

    def _update_playlist(self) -> m3u8.M3U8:
        """Fetch the playlist and update the master playlist if needed."""
        playlist = self._fetch_manifest(self._profile_selector(self._master_playlist.playlists, key=lambda p: p.stream_info.bandwidth).absolute_uri if self._master_playlist else self._playlist_url)

        if playlist.is_variant:
            self._master_playlist = playlist
            playlist = self._fetch_manifest(self._profile_selector(self._master_playlist.playlists, key=lambda p: p.stream_info.bandwidth).absolute_uri)

        return playlist

    def step(self) -> bool:
        """
        Advance the session by one step. Return True if the session is still active, False if it has ended.
        :return: True if the session is still active, False if it has ended.
        """
        try:
            playlist = self._update_playlist()

            # first run?
            if self._last_media_sequence_seen is None:
                # create player
                self._player = ABRPlayer()

                # fill buffer ASAP
                self._player.start_buffering()
                for media_segment in playlist.segments[-3:]:  # TODO: clarify buffer target (3)
                    if media_segment.media_sequence is None or media_segment.duration is None:
                        raise Exception(f"Invalid media segment: {media_segment}")

                    # download segment (no retry during buffering), simulate buffering
                    self._fetch_segment(media_segment.absolute_uri, name=media_segment.base_uri.rstrip("/").rsplit("/", 1)[-1])
                    self._player.buffer(Segment(media_segment.media_sequence, media_segment.duration))
                    self._last_media_sequence_seen = media_segment.media_sequence

                # initialize next fetch timestamp
                self._fetch_playlist_at = time.monotonic() + playlist.target_duration

                # start play
                startup_duration, startup_time_wall_clock = self._player.start_playing()
                self.startup_time_event.fire(environment=self._environment, duration=startup_duration, userid=self.user_id, time=startup_time_wall_clock)
            else:
                # identify next segment (according to rfc8216 6.3.5)
                while (media_segment := next(
                        (s for s in playlist.segments if s.media_sequence > self._last_media_sequence_seen),
                        None)) is None:
                    if playlist.is_endlist:
                        logging.info(f"End of playlist reached for user {self.user_id}, stopping session")
                        return False

                    # stale playlist, add back pressure my moving the manifest fetch in the middle of the target duration, so that we don't hammer the server with requests
                    self._fetch_playlist_at += playlist.target_duration / 2
                    logging.warning(f"Stale playlist for user {self.user_id}, retrying in {playlist.target_duration / 2} seconds...")

                    # retry
                    self._sleep(playlist.target_duration / 2)
                    self._player.advance()
                    playlist = self._update_playlist()
                    self._player.advance()

                if media_segment.media_sequence is None or media_segment.duration is None:
                    raise Exception(f"Invalid media segment: {media_segment}")

                # fetch that segment with retry
                wait = 0.100
                while True:  # if consequent segment fetches are unsuccessful, we leave this loop with a buffer underrun
                    try:
                        buffer_level = self._player.advance()
                        self._fetch_segment(media_segment.absolute_uri, name=media_segment.base_uri.rstrip("/").rsplit("/", 1)[-1])
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

        except PlayerError as e:
            self.player_error_event.fire(userid=self.user_id, time=e.time_wc, exception=e)
        except ManifestError as e:
            self.manifest_error_event.fire(userid=self.user_id, time=time.time(), exception=e)
        except (CatchResponseError, LocustBadStatusCode) as e:
            self.http_error_event.fire(userid=self.user_id, time=time.time(), exception=e)

        # we simulate a use-case, where clients are constantly trying to rebuffer, so we just reset the playback state and continue

        # reset playback to trigger rebuffering
        self._init_state()
        self._player = None

        # wait a bit before retrying
        self._sleep(1)
        return True
