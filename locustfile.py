from gevent import sleep
from locust import FastHttpUser, constant, task

from streaming import HLSSession


class HLSUser(FastHttpUser):
    wait_time = constant(0)

    def on_start(self) -> None:
        if self.host is None:
            raise ValueError("host must be set for HLSUser")

        self._stream = HLSSession(self.host, self.client, sleep)

    def on_stop(self) -> None:
        self._stream.stop()

    @task
    def stream(self):
        if not self._stream.step():
            print(f"exit user {self._stream.userid}")
            self.stop(force=True)
