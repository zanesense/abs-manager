from __future__ import annotations

import json
import signal
import threading
from pathlib import Path
from typing import Any

from .deadlock import DeadlockManager
from .disk import AllocationMethod, DiskManager
from .filesystem import FileSystem
from .journal import Journal
from .scheduler import Scheduler


class Kernel:
    def __init__(self, root: str | Path = ".") -> None:
        self.root_path = Path(root)
        self.state_path = self.root_path / "fs_state.json"
        self.disk = DiskManager(self.root_path / "disk.bin")
        self.fs = FileSystem(self.disk, host_root=self.root_path)
        self.scheduler = Scheduler()
        self.deadlock = DeadlockManager()
        self.journal = Journal(self.root_path / "journal.log")
        self.cwd = "/"
        self.user = "root"
        self.io_pool = threading.Semaphore(8)
        self.mutexes: dict[str, threading.Lock] = {}
        self.semaphores: dict[str, threading.Semaphore] = {}
        self._lock = threading.RLock()
        self.load()
        try:
            signal.signal(signal.SIGTERM, lambda *_: self.flush())
        except ValueError:
            pass

    def load(self) -> None:
        if not self.state_path.exists():
            self.flush()
            return
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.disk.load_state(data.get("disk", {}))
        self.fs.load_state(data.get("fs", {}))
        self.cwd = data.get("cwd", "/")
        self.user = data.get("user", "root")
        self.fs.user = self.user

    def flush(self) -> None:
        self.root_path.mkdir(parents=True, exist_ok=True)
        data = {"disk": self.disk.dump_state(), "fs": self.fs.dump_state(), "cwd": self.cwd, "user": self.user}
        self.state_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def mutate(self, op: str, payload: dict[str, Any], fn: Any) -> Any:
        with self._lock, self.io_pool:
            result = fn()
            self.journal.append(op, payload)
            self.flush()
            return result

    def cd(self, path: str) -> str:
        node = self.fs.resolve(path, self.cwd)
        if node.type != "dir":
            from .errors import MiniOSError
            raise MiniOSError(f"{path}: not a directory", 1)
        self.cwd = self.fs.norm(path, self.cwd)
        self.flush()
        return self.cwd

    def mkfs(self, num_blocks: int = 256, block_size: int = 256) -> None:
        def work() -> None:
            self.disk.configure(num_blocks, block_size)
            self.fs = FileSystem(self.disk, host_root=self.root_path)
            self.cwd = "/"
        self.mutate("mkfs", {"num_blocks": num_blocks, "block_size": block_size}, work)

    def alloc(self, path: str, method: AllocationMethod) -> None:
        def work() -> None:
            node = self.fs.resolve(path, self.cwd)
            if node.file_id:
                host = self.fs.host_path(path, self.cwd)
                data = host.read_bytes() if host.exists() and host.is_file() else self.disk.read(node.file_id)
                self.disk.allocate(node.file_id, data, method)
        self.mutate("alloc", {"path": path, "method": method}, work)

    def sem(self, name: str, value: int) -> str:
        self.semaphores[name] = threading.Semaphore(value)
        return f"semaphore {name}={value}"

    def mutex(self, name: str, action: str) -> str:
        lock = self.mutexes.setdefault(name, threading.Lock())
        if action == "lock":
            lock.acquire()
            return f"mutex {name} locked"
        lock.release()
        return f"mutex {name} unlocked"
