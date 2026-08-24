from __future__ import annotations

from abc import abstractmethod, ABC
from types import TracebackType
from typing import Self


class BackgroundTask(ABC):
    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def start(self) -> None: ...

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        self.close()
        if exc_type is not None and exc_val is not None:
            raise exc_val
