from abc import ABC


class PlayerError(Exception, ABC):
    def __init__(self, at: float, *args: object):
        super().__init__(*args)
        self._at = at

    @property
    def at(self) -> float:
        return self._at


class BufferUnderrunError(PlayerError):
    pass
