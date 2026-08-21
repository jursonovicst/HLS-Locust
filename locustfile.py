import statistics

import gevent
from locust import FastHttpUser, constant, task
from locust import events
from locust.event import EventHook

from streaming import HLSSession

buffer_level_event = EventHook()
startup_time_event = EventHook()

# In-memory store for collected samples
_buffer_level_samples: list[float] = []
_startup_time_samples: list[float] = []


def _calc_stats(samples: list[float]) -> dict:
    """Return common summary stats with safe handling for empty input."""
    if not samples:
        return {
            "samples": 0,
            "mean": None,
            "median": None,
            "p05": None,
            "p95": None,
            "min": None,
            "max": None,
        }

    s = sorted(samples)
    n = len(s)
    p05_idx = min(n - 1, max(0, int((n - 1) * 0.05)))
    p95_idx = min(n - 1, max(0, int((n - 1) * 0.95)))
    return {
        "samples": n,
        "mean": statistics.mean(s),
        "median": statistics.median(s),
        "p05": s[p05_idx],
        "p95": s[p95_idx],
        "min": s[0],
        "max": s[-1],
    }


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}s"


def _on_buffer_level(level: float, userid: str) -> None:
    """Listener: store every buffer level sample."""
    _buffer_level_samples.append(level)


def _on_startup_time(time: float, userid: str) -> None:
    """Listener: store every buffer level sample."""
    _startup_time_samples.append(time)


buffer_level_event.add_listener(_on_buffer_level)
startup_time_event.add_listener(_on_startup_time)


@events.init.add_listener
def on_locust_init(environment, **kwargs):
    environment.buffer_level_event = buffer_level_event
    environment.startup_time_event = startup_time_event


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Print metric matrix for buffer level and startup time when the test ends."""
    buffer_stats = _calc_stats(_buffer_level_samples)
    startup_stats = _calc_stats(_startup_time_samples)

    if buffer_stats["samples"] == 0 and startup_stats["samples"] == 0:
        print("[custom-metrics] No samples collected.")
        return

    print("\n── Custom Metrics Matrix ─────────────────────────────────────────────────────────")
    print(f"{'Metric':<14} {'Samples':>8} {'Mean':>10} {'Median':>10} {'p05':>10} {'p95':>10} {'Min':>10} {'Max':>10}")
    print(
        f"{'buffer_level':<14} {buffer_stats['samples']:>8} {_fmt(buffer_stats['mean']):>10} {_fmt(buffer_stats['median']):>10} {_fmt(buffer_stats['p05']):>10} {_fmt(buffer_stats['p95']):>10} {_fmt(buffer_stats['min']):>10} {_fmt(buffer_stats['max']):>10}")
    print(
        f"{'startup_time':<14} {startup_stats['samples']:>8} {_fmt(startup_stats['mean']):>10} {_fmt(startup_stats['median']):>10} {_fmt(startup_stats['p05']):>10} {_fmt(startup_stats['p95']):>10} {_fmt(startup_stats['min']):>10} {_fmt(startup_stats['max']):>10}")
    print("──────────────────────────────────────────────────────────────────────────────────\n")


# ── Locust User ────────────────────────────────────────────────────────────────
class HLSUser(FastHttpUser):
    wait_time = constant(0)

    def on_start(self) -> None:
        if self.host is None:
            raise ValueError("host must be set for HLSUser")

        self._stream = HLSSession(self.host, self.client, gevent.sleep)

    @task
    def stream(self):
        if not self._stream.step():
            print(f"exit user {self._stream.userid}")
            self.stop(force=True)
