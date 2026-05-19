from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: str | Path = "journal.log") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.path.touch(exist_ok=True)

    def append(self, op: str, payload: dict[str, Any] | None = None) -> None:
        entry = {"ts": time.time(), "op": op, "payload": payload or {}}
        line = json.dumps(entry, sort_keys=True)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rows.append(json.loads(line))
            return rows

    def tail(self, n: int = 20) -> list[dict[str, Any]]:
        return self.entries()[-n:]

    def replay(self) -> list[str]:
        return [f"{row['op']} {json.dumps(row.get('payload', {}), sort_keys=True)}" for row in self.entries()]

    def clear(self) -> None:
        with self._lock:
            self.path.write_text("", encoding="utf-8")

    def verify(self) -> tuple[bool, str]:
        try:
            self.entries()
        except Exception as exc:  # pragma: no cover - exact JSON error varies
            return False, str(exc)
        return True, "journal ok"
