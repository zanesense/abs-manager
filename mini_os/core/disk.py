from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any, Literal

from .errors import NoSpace

AllocationMethod = Literal["contiguous", "linked", "indexed"]


class DiskManager:
    def __init__(
        self,
        disk_path: str | Path = "disk.bin",
        num_blocks: int = 256,
        block_size: int = 256,
    ) -> None:
        self.disk_path = Path(disk_path)
        self.num_blocks = num_blocks
        self.block_size = block_size
        self.bitmap: list[bool] = [False] * num_blocks
        self.allocations: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._sem = threading.Semaphore(8)
        self._ensure_disk()

    def _ensure_disk(self) -> None:
        self.disk_path.parent.mkdir(parents=True, exist_ok=True)
        expected = self.num_blocks * self.block_size
        if not self.disk_path.exists() or self.disk_path.stat().st_size != expected:
            with self.disk_path.open("wb") as fh:
                fh.truncate(expected)

    def configure(self, num_blocks: int, block_size: int) -> None:
        with self._lock:
            self.num_blocks = num_blocks
            self.block_size = block_size
            self.bitmap = [False] * num_blocks
            self.allocations.clear()
            with self.disk_path.open("wb") as fh:
                fh.truncate(num_blocks * block_size)

    def load_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.num_blocks = int(state.get("num_blocks", self.num_blocks))
            self.block_size = int(state.get("block_size", self.block_size))
            self.bitmap = [bool(x) for x in state.get("bitmap", [False] * self.num_blocks)]
            self.allocations = dict(state.get("allocations", {}))
            if len(self.bitmap) != self.num_blocks:
                self.bitmap = (self.bitmap + [False] * self.num_blocks)[: self.num_blocks]
            self._ensure_disk()

    def dump_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "num_blocks": self.num_blocks,
                "block_size": self.block_size,
                "bitmap": self.bitmap,
                "allocations": self.allocations,
            }

    def free_blocks(self) -> int:
        return self.bitmap.count(False)

    def used_blocks(self) -> int:
        return self.bitmap.count(True)

    def blocks_needed(self, size: int, method: AllocationMethod) -> int:
        data_blocks = max(1, math.ceil(max(size, 1) / self.block_size))
        return data_blocks + (1 if method == "indexed" else 0)

    def allocate(self, file_id: str, data: bytes, method: AllocationMethod = "indexed") -> None:
        with self._lock:
            if file_id in self.allocations:
                self.free(file_id)
            blocks_needed = self.blocks_needed(len(data), method)
            blocks = self._pick_blocks(blocks_needed, method)
            for block in blocks:
                self.bitmap[block] = True
            meta: dict[str, Any] = {
                "method": method,
                "blocks": blocks,
                "size": len(data),
                "index_block": blocks[0] if method == "indexed" else None,
            }
            self.allocations[file_id] = meta
            self._write_blocks(blocks, data, meta)

    def reallocate(self, file_id: str, method: AllocationMethod) -> None:
        data = self.read(file_id)
        self.allocate(file_id, data, method)

    def free(self, file_id: str) -> None:
        with self._lock:
            meta = self.allocations.pop(file_id, None)
            if not meta:
                return
            for block in meta.get("blocks", []):
                if 0 <= block < self.num_blocks:
                    self.bitmap[block] = False
                    self._zero_block(block)

    def read(self, file_id: str) -> bytes:
        with self._lock:
            if file_id not in self.allocations:
                return b""
            meta = self.allocations[file_id]
            blocks = list(meta["blocks"])
            if meta.get("method") == "indexed":
                blocks = blocks[1:]
            raw = bytearray()
            with self._sem, self.disk_path.open("rb") as fh:
                for block in blocks:
                    fh.seek(block * self.block_size)
                    raw.extend(fh.read(self.block_size))
            return bytes(raw[: int(meta.get("size", 0))])

    def _pick_blocks(self, count: int, method: AllocationMethod) -> list[int]:
        if self.free_blocks() < count:
            raise NoSpace()
        if method == "contiguous":
            run: list[int] = []
            for idx, used in enumerate(self.bitmap):
                run = [] if used else run + [idx]
                if len(run) == count:
                    return run
            raise NoSpace("contiguous allocation")
        return [idx for idx, used in enumerate(self.bitmap) if not used][:count]

    def _write_blocks(self, blocks: list[int], data: bytes, meta: dict[str, Any]) -> None:
        data_blocks = blocks[1:] if meta["method"] == "indexed" else blocks
        with self._sem, self.disk_path.open("r+b") as fh:
            if meta["method"] == "indexed":
                index_payload = ",".join(str(x) for x in data_blocks).encode()
                fh.seek(blocks[0] * self.block_size)
                fh.write(index_payload[: self.block_size].ljust(self.block_size, b"\0"))
            for offset, block in enumerate(data_blocks):
                chunk = data[offset * self.block_size : (offset + 1) * self.block_size]
                fh.seek(block * self.block_size)
                fh.write(chunk.ljust(self.block_size, b"\0"))

    def _zero_block(self, block: int) -> None:
        with self._sem, self.disk_path.open("r+b") as fh:
            fh.seek(block * self.block_size)
            fh.write(b"\0" * self.block_size)

    def fragmentation(self) -> dict[str, Any]:
        with self._lock:
            free_runs: list[int] = []
            current = 0
            for used in self.bitmap + [True]:
                if used:
                    if current:
                        free_runs.append(current)
                    current = 0
                else:
                    current += 1
            used = self.used_blocks()
            extents = 0
            for meta in self.allocations.values():
                last = None
                for block in meta.get("blocks", []):
                    if last is None or block != last + 1:
                        extents += 1
                    last = block
            return {
                "free_runs": free_runs,
                "largest_free_run": max(free_runs, default=0),
                "fragmentation_percent": (
                    0 if not free_runs else round((1 - max(free_runs) / max(sum(free_runs), 1)) * 100, 2)
                ),
                "extents": extents,
                "used_blocks": used,
                "free_blocks": self.free_blocks(),
            }

    def compact(self) -> None:
        with self._lock:
            snapshot = [(fid, self.read(fid), meta["method"]) for fid, meta in sorted(self.allocations.items())]
            self.bitmap = [False] * self.num_blocks
            self.allocations.clear()
            with self.disk_path.open("r+b") as fh:
                fh.seek(0)
                fh.write(b"\0" * (self.num_blocks * self.block_size))
            for fid, data, method in snapshot:
                self.allocate(fid, data, method)

    def fsck(self) -> tuple[bool, list[str]]:
        errors: list[str] = []
        seen: set[int] = set()
        for fid, meta in self.allocations.items():
            for block in meta.get("blocks", []):
                if block < 0 or block >= self.num_blocks:
                    errors.append(f"{fid}: block {block} out of range")
                if block in seen:
                    errors.append(f"{fid}: block {block} double allocated")
                seen.add(block)
        for idx, used in enumerate(self.bitmap):
            if used != (idx in seen):
                errors.append(f"bitmap mismatch at block {idx}")
        return not errors, errors

    def mount(self) -> None:
        self._ensure_disk()

    def umount(self) -> None:
        if hasattr(os, "sync"):
            os.sync()
