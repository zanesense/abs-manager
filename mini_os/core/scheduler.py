from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

State = Literal["ready", "running", "blocked", "done", "killed"]


@dataclass
class Process:
    pid: int
    name: str
    burst: int
    priority: int = 5
    remaining: int = 0
    state: State = "ready"
    age: int = 0
    created: float = field(default_factory=time.time)
    ticks: int = 0

    def __post_init__(self) -> None:
        if self.remaining <= 0:
            self.remaining = self.burst


class Scheduler:
    def __init__(self, quantum: int = 2) -> None:
        self.quantum = quantum
        self.processes: dict[int, Process] = {}
        self.history: list[dict[str, int | str]] = []
        self._ids = itertools.count(100)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._running = threading.Event()

    def spawn(self, name: str, burst: int, priority: int = 5) -> Process:
        with self._lock:
            proc = Process(next(self._ids), name, max(1, burst), priority)
            self.processes[proc.pid] = proc
            return proc

    def kill(self, pid: int) -> bool:
        with self._lock:
            proc = self.processes.get(pid)
            if not proc:
                return False
            proc.state = "killed"
            return True

    def nice(self, pid: int, priority: int) -> None:
        with self._lock:
            self.processes[pid].priority = priority

    def tick_rr(self, n: int = 1) -> list[Process]:
        ran: list[Process] = []
        with self._lock:
            for _ in range(n):
                ready = [p for p in self.processes.values() if p.state in {"ready", "running"} and p.remaining > 0]
                if not ready:
                    break
                ready.sort(key=lambda p: (p.ticks, p.created))
                proc = ready[0]
                slice_ticks = min(self.quantum, proc.remaining)
                proc.state = "running"
                proc.remaining -= slice_ticks
                proc.ticks += slice_ticks
                proc.state = "done" if proc.remaining == 0 else "ready"
                self.history.append({"algo": "rr", "pid": proc.pid, "ticks": slice_ticks})
                ran.append(proc)
        return ran

    def tick_priority(self, n: int = 1) -> list[Process]:
        ran: list[Process] = []
        with self._lock:
            for _ in range(n):
                ready = [p for p in self.processes.values() if p.state in {"ready", "running"} and p.remaining > 0]
                if not ready:
                    break
                for p in ready:
                    p.age += 1
                proc = min(ready, key=lambda p: (p.priority - (p.age * 5), p.created))
                proc.state = "running"
                proc.remaining -= 1
                proc.ticks += 1
                proc.age = 0
                proc.state = "done" if proc.remaining == 0 else "ready"
                self.history.append({"algo": "priority", "pid": proc.pid, "ticks": 1})
                ran.append(proc)
        return ran

    def table(self) -> list[Process]:
        with self._lock:
            return sorted(self.processes.values(), key=lambda p: p.pid)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self._running.set()
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._running.clear()

    def _loop(self) -> None:
        while True:
            self._running.wait()
            self.tick_rr(1)
            time.sleep(0.2)
