import logging
import os

import gevent
from locust import FastHttpUser, constant, task
from locust.exception import StopUser

from streaming import HLSSession


@HLSSession.listen(HLSSession.buffer_underrun_event)
def _on_buffer_underrun(environment, userid: str, time: float) -> None:
    """Listener: store every buffer underrun sample."""
    logging.warning(f"Buffer underrun for user {userid} at {time:.3f}s")


@HLSSession.listen(HLSSession.startup_time_event)
def _on_startup_time(environment, duration: float, userid: str, time: float) -> None:
    """Listener: store every startup time sample."""
    logging.info(f"Startup time for user {userid}: {duration:.3f}s")


# ── Locust User ────────────────────────────────────────────────────────────────
class HLSUser(FastHttpUser):
    # proxy support
    proxy_host = os.getenv("HTTP_PROXY_HOST", None)
    proxy_port = int(os.getenv("HTTP_PROXY_PORT", '0')) or None

    wait_time = constant(0)  # HLSUser implements its own pacing, so we don't want Locust to add any additional wait time

    def on_start(self) -> None:
        if self.host is None:
            raise StopUser("Host must be the playlist URL (e.g. http://localhost:8000/playlist.m3u8)")
        self._stream = HLSSession(self.host, min, self.client, self.environment, gevent.sleep)

    @task
    def stream(self):
        if not self._stream.step():
            raise StopUser()
