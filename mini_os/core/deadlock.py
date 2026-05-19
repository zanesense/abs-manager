from __future__ import annotations

import threading

from .errors import MiniOSError


class DeadlockManager:
    def __init__(self, total: list[int] | None = None) -> None:
        self.total = total or [10, 5, 7]
        self.maximum: dict[int, list[int]] = {}
        self.allocation: dict[int, list[int]] = {}
        self._lock = threading.RLock()

    def available(self) -> list[int]:
        allocated = [0] * len(self.total)
        for vals in self.allocation.values():
            for i, val in enumerate(vals):
                allocated[i] += val
        return [t - a for t, a in zip(self.total, allocated)]

    def register(self, pid: int, maximum: list[int]) -> None:
        with self._lock:
            self._validate_len(maximum)
            if any(v > t for v, t in zip(maximum, self.total)):
                raise MiniOSError("bregister: maximum exceeds total resources", 1)
            self.maximum[pid] = list(maximum)
            self.allocation.setdefault(pid, [0] * len(self.total))

    def request(self, pid: int, request: list[int]) -> bool:
        with self._lock:
            self._validate_pid(pid)
            self._validate_len(request)
            need = self.need(pid)
            if any(r > n for r, n in zip(request, need)):
                raise MiniOSError("brequest: request exceeds declared maximum", 1)
            if any(r > a for r, a in zip(request, self.available())):
                return False
            self.allocation[pid] = [a + r for a, r in zip(self.allocation[pid], request)]
            if self.is_safe():
                return True
            self.allocation[pid] = [a - r for a, r in zip(self.allocation[pid], request)]
            return False

    def release(self, pid: int, release: list[int]) -> None:
        with self._lock:
            self._validate_pid(pid)
            self._validate_len(release)
            if any(r > a for r, a in zip(release, self.allocation[pid])):
                raise MiniOSError("brelease: release exceeds allocation", 1)
            self.allocation[pid] = [a - r for a, r in zip(self.allocation[pid], release)]

    def need(self, pid: int) -> list[int]:
        return [m - a for m, a in zip(self.maximum[pid], self.allocation[pid])]

    def is_safe(self) -> bool:
        work = self.available()
        finish = {pid: False for pid in self.maximum}
        while True:
            progressed = False
            for pid in self.maximum:
                if not finish[pid] and all(n <= w for n, w in zip(self.need(pid), work)):
                    work = [w + a for w, a in zip(work, self.allocation[pid])]
                    finish[pid] = True
                    progressed = True
            if not progressed:
                return all(finish.values())

    def table(self) -> list[dict[str, object]]:
        return [
            {"pid": pid, "max": self.maximum[pid], "alloc": self.allocation[pid], "need": self.need(pid)}
            for pid in sorted(self.maximum)
        ]

    def _validate_pid(self, pid: int) -> None:
        if pid not in self.maximum:
            raise MiniOSError(f"{pid}: process not registered", 1)

    def _validate_len(self, values: list[int]) -> None:
        if len(values) != len(self.total):
            raise MiniOSError(f"resources: expected {len(self.total)} values", 2)
