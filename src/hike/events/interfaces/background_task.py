from __future__ import annotations

from abc import abstractmethod, ABC
from typing import Callable, NoReturn

# blocking
type Task = Callable[[], NoReturn]


class IBackgroundTasks(ABC):
    @abstractmethod
    def cleanup(self) -> None:
        """Release any resources held by this task after it has stopped.

        Called only as a safety net when the task exits unexpectedly — not as a
        graceful-shutdown signal.  Implementations should be idempotent and must
        not raise.
        """
        ...

    @abstractmethod
    def tasks(self) -> list[Task]:
        """Return the blocking callables that should be run in background threads.

        Each returned callable runs until the underlying broker connection is lost
        or an unrecoverable error occurs.  They are not expected to return under
        normal operation.
        """
        ...
