class PlayerError(Exception):
    def __init__(self, *args: object, time_wc: float):
        super().__init__(*args)
        self._time_wc = time_wc

    @property
    def time_wc(self) -> float:
        return self._time_wc


class BufferUnderrunError(PlayerError):
    pass
