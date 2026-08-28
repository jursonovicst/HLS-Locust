from abc import ABC


class PlayerError(Exception, ABC):
    def __init__(self, at_wall_clock: float, *args: object):
        super().__init__(*args)
        self._at_wall_clock = at_wall_clock

    @property
    def at_wall_clock(self) -> float:
        return self._at_wall_clock


class BufferUnderrunError(PlayerError):
    pass
