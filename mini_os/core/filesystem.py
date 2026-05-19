from __future__ import annotations

import fnmatch
import posixpath
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .disk import AllocationMethod, DiskManager
from .errors import ExistsErrorOS, MiniOSError, NotEmpty, PermissionDenied

NodeType = Literal["file", "dir"]


@dataclass
class Node:
    name: str
    type: NodeType
    owner: str = "root"
    group: str = "root"
    mode: int = 0o755
    ctime: float = field(default_factory=time.time)
    mtime: float = field(default_factory=time.time)
    atime: float = field(default_factory=time.time)
    size: int = 0
    file_id: str | None = None
    children: dict[str, "Node"] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode
        data["children"] = {k: v.to_dict() for k, v in self.children.items()}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Node":
        node = cls(
            name=data["name"],
            type=data["type"],
            owner=data.get("owner", "root"),
            group=data.get("group", "root"),
            mode=int(data.get("mode", 0o755)),
            ctime=float(data.get("ctime", time.time())),
            mtime=float(data.get("mtime", time.time())),
            atime=float(data.get("atime", time.time())),
            size=int(data.get("size", 0)),
            file_id=data.get("file_id"),
        )
        node.children = {k: cls.from_dict(v) for k, v in data.get("children", {}).items()}
        return node


class FileSystem:
    INTERNAL_FILES = {"disk.bin", "fs_state.json", "journal.log", ".absmanager_history"}

    def __init__(
        self,
        disk: DiskManager,
        user: str = "root",
        group: str = "root",
        host_root: str | Path | None = None,
    ) -> None:
        self.disk = disk
        self.user = user
        self.group = group
        self.host_root = Path(host_root).resolve() if host_root is not None else None
        self.root = Node("/", "dir")
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self.sync_from_host()

    def dump_state(self) -> dict[str, Any]:
        with self._lock:
            return {"root": self.root.to_dict(), "user": self.user, "group": self.group}

    def load_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.root = Node.from_dict(state.get("root", self.root.to_dict()))
            self.user = state.get("user", self.user)
            self.group = state.get("group", self.group)
            self.sync_from_host()

    def norm(self, path: str, cwd: str = "/") -> str:
        if not path:
            path = "."
        base = path if path.startswith("/") else posixpath.join(cwd, path)
        clean = posixpath.normpath(base)
        return "/" if clean == "." else clean

    def resolve(self, path: str, cwd: str = "/") -> Node:
        self.sync_from_host_path(path, cwd)
        full = self.norm(path, cwd)
        if full == "/":
            return self.root
        node = self.root
        for part in full.strip("/").split("/"):
            if node.type != "dir" or part not in node.children:
                raise MiniOSError(f"{full}: no such file or directory", 1)
            node = node.children[part]
        return node

    def parent(self, path: str, cwd: str = "/") -> tuple[Node, str, str]:
        full = self.norm(path, cwd)
        if full == "/":
            raise MiniOSError("/: invalid target", 1)
        parent_path, name = posixpath.split(full)
        parent = self.resolve(parent_path or "/", "/")
        if parent.type != "dir":
            raise MiniOSError(f"{parent_path}: not a directory", 1)
        return parent, name, full

    def check(self, node: Node, op: str) -> None:
        if self.user == "root":
            return
        shift = 6 if node.owner == self.user else 3 if node.group == self.group else 0
        need = {"read": 4, "write": 2, "exec": 1, "delete": 2}[op]
        if ((node.mode >> shift) & need) != need:
            raise PermissionDenied(node.name)

    def mkdir(self, path: str, cwd: str = "/", parents: bool = False) -> str:
        with self._lock:
            full = self.norm(path, cwd)
            if full == "/":
                return "/"
            node = self.root
            built = ""
            parts = full.strip("/").split("/")
            for idx, part in enumerate(parts):
                built += "/" + part
                if part not in node.children:
                    if not parents and idx != len(parts) - 1:
                        raise MiniOSError(f"{built}: no such file or directory", 1)
                    self.check(node, "write")
                    node.children[part] = Node(part, "dir", self.user, self.group, 0o755)
                    self.host_path(built).mkdir(exist_ok=True)
                elif idx == len(parts) - 1 and not parents:
                    raise ExistsErrorOS(full)
                node = node.children[part]
                if node.type != "dir":
                    raise MiniOSError(f"{built}: not a directory", 1)
            self.host_path(full).mkdir(parents=parents, exist_ok=True)
            return full

    def touch(self, path: str, cwd: str = "/", method: AllocationMethod = "indexed") -> str:
        with self._lock:
            parent, name, full = self.parent(path, cwd)
            self.check(parent, "write")
            if name in parent.children:
                node = parent.children[name]
                self.check(node, "write")
                node.mtime = time.time()
                self.host_path(full).touch(exist_ok=True)
                return full
            fid = uuid.uuid4().hex
            node = Node(name, "file", self.user, self.group, 0o644, file_id=fid)
            parent.children[name] = node
            self.host_path(full).parent.mkdir(parents=True, exist_ok=True)
            self.host_path(full).touch(exist_ok=True)
            self.disk.allocate(fid, b"", method)
            return full

    def write(
        self,
        path: str,
        data: str | bytes,
        cwd: str = "/",
        append: bool = False,
        method: AllocationMethod = "indexed",
    ) -> str:
        with self._lock:
            try:
                node = self.resolve(path, cwd)
            except MiniOSError:
                self.touch(path, cwd, method)
                node = self.resolve(path, cwd)
            if node.type != "file":
                raise MiniOSError(f"{path}: is a directory", 1)
            self.check(node, "write")
            if not node.file_id:
                node.file_id = uuid.uuid4().hex
            host = self.host_path(self.norm(path, cwd))
            old = host.read_bytes() if append and host.exists() else b""
            raw = data.encode() if isinstance(data, str) else data
            payload = old + raw
            self.disk.allocate(node.file_id or "", payload, self.method(node))
            host.parent.mkdir(parents=True, exist_ok=True)
            host.write_bytes(payload)
            now = time.time()
            node.size = len(payload)
            node.mtime = now
            node.atime = now
            self._cond.notify_all()
            return self.norm(path, cwd)

    def read(self, path: str, cwd: str = "/") -> str:
        with self._lock:
            node = self.resolve(path, cwd)
            if node.type != "file":
                raise MiniOSError(f"{path}: is a directory", 1)
            self.check(node, "read")
            node.atime = time.time()
            host = self.host_path(self.norm(path, cwd))
            if host.exists():
                return host.read_text(encoding="utf-8", errors="replace")
            return self.disk.read(node.file_id or "").decode(errors="replace")

    def listdir(self, path: str = ".", cwd: str = "/", all_: bool = False) -> list[Node]:
        with self._lock:
            self.sync_from_host_path(path, cwd)
            node = self.resolve(path, cwd)
            if node.type != "dir":
                return [node]
            self.check(node, "read")
            rows = list(node.children.values())
            if all_:
                rows = [Node(".", "dir"), Node("..", "dir")] + rows
            return sorted(rows, key=lambda x: (x.type != "dir", x.name))

    def remove(self, path: str, cwd: str = "/", recursive: bool = False) -> str:
        with self._lock:
            parent, name, full = self.parent(path, cwd)
            self.check(parent, "write")
            if name not in parent.children:
                raise MiniOSError(f"{full}: no such file or directory", 1)
            node = parent.children[name]
            self.check(node, "delete")
            if node.type == "dir" and node.children and not recursive:
                raise NotEmpty(full)
            self._free_node(node)
            host = self.host_path(full)
            if host.is_dir():
                shutil.rmtree(host)
            elif host.exists():
                host.unlink()
            del parent.children[name]
            return full

    def _free_node(self, node: Node) -> None:
        if node.type == "file" and node.file_id:
            self.disk.free(node.file_id)
        for child in list(node.children.values()):
            self._free_node(child)

    def chmod(self, path: str, mode: int, cwd: str = "/") -> None:
        node = self.resolve(path, cwd)
        if self.user != "root" and node.owner != self.user:
            raise PermissionDenied(path)
        node.mode = mode
        node.mtime = time.time()

    def chown(self, path: str, owner: str, group: str | None = None, cwd: str = "/") -> None:
        if self.user != "root":
            raise PermissionDenied(path)
        node = self.resolve(path, cwd)
        node.owner = owner
        if group:
            node.group = group

    def move(self, src: str, dst: str, cwd: str = "/") -> str:
        with self._lock:
            src_parent, src_name, _ = self.parent(src, cwd)
            node = src_parent.children.get(src_name)
            if node is None:
                raise MiniOSError(f"{src}: no such file or directory", 1)
            dst_parent, dst_name, full = self.parent(dst, cwd)
            self.check(src_parent, "write")
            self.check(dst_parent, "write")
            src_host = self.host_path(self.norm(src, cwd))
            dst_host = self.host_path(full)
            dst_host.parent.mkdir(parents=True, exist_ok=True)
            if src_host.exists():
                shutil.move(str(src_host), str(dst_host))
            node.name = dst_name
            dst_parent.children[dst_name] = node
            del src_parent.children[src_name]
            return full

    def copy(self, src: str, dst: str, cwd: str = "/") -> str:
        src_node = self.resolve(src, cwd)
        if src_node.type == "dir":
            raise MiniOSError(f"{src}: is a directory", 1)
        data = self.host_path(self.norm(src, cwd)).read_bytes()
        return self.write(dst, data, cwd)

    def find(self, pattern: str, path: str = "/", cwd: str = "/") -> list[str]:
        start = self.resolve(path, cwd)
        root_path = self.norm(path, cwd)
        results: list[str] = []
        def walk(node: Node, current: str) -> None:
            if fnmatch.fnmatch(node.name, pattern) or fnmatch.fnmatch(current, pattern):
                results.append(current)
            for child in node.children.values():
                walk(child, posixpath.join(current, child.name) if current != "/" else "/" + child.name)
        walk(start, root_path)
        return sorted(set(results))

    def method(self, node: Node) -> AllocationMethod:
        meta = self.disk.allocations.get(node.file_id or "", {})
        return meta.get("method", "indexed")

    def walk(self, path: str = "/", cwd: str = "/") -> list[tuple[str, Node]]:
        self.sync_from_host_path(path, cwd)
        node = self.resolve(path, cwd)
        root_path = self.norm(path, cwd)
        rows: list[tuple[str, Node]] = []
        def rec(n: Node, p: str) -> None:
            rows.append((p, n))
            for child in sorted(n.children.values(), key=lambda x: x.name):
                rec(child, posixpath.join(p, child.name) if p != "/" else "/" + child.name)
        rec(node, root_path)
        return rows

    def host_path(self, path: str, cwd: str = "/") -> Path:
        if self.host_root is None:
            raise MiniOSError("host filesystem is not configured", 1)
        full = self.norm(path, cwd)
        rel = full.strip("/")
        candidate = (self.host_root / rel).resolve() if rel else self.host_root
        if candidate != self.host_root and self.host_root not in candidate.parents:
            raise MiniOSError(f"{path}: escapes working directory", 13)
        return candidate

    def sync_from_host(self) -> None:
        if self.host_root is None or not self.host_root.exists():
            return
        self._sync_dir(self.host_root, self.root)

    def sync_from_host_path(self, path: str, cwd: str = "/") -> None:
        if self.host_root is None:
            return
        full = self.norm(path, cwd)
        parts = [] if full == "/" else full.strip("/").split("/")
        current_path = self.host_root
        current_node = self.root
        self._sync_dir(current_path, current_node)
        for part in parts:
            current_path = current_path / part
            if part not in current_node.children and current_path.exists():
                current_node.children[part] = self._node_from_host(current_path)
            if part not in current_node.children:
                return
            current_node = current_node.children[part]
            if current_path.is_dir():
                self._sync_dir(current_path, current_node)

    def _sync_dir(self, host_dir: Path, node: Node) -> None:
        if node.type != "dir" or not host_dir.exists() or not host_dir.is_dir():
            return
        host_names = {child.name for child in host_dir.iterdir() if child.name not in self.INTERNAL_FILES}
        for stale in set(node.children) - host_names:
            self._free_node(node.children[stale])
            del node.children[stale]
        for child in host_dir.iterdir():
            if child.name in self.INTERNAL_FILES or child.name == "__pycache__":
                continue
            existing = node.children.get(child.name)
            if existing is None or existing.type != ("dir" if child.is_dir() else "file"):
                node.children[child.name] = self._node_from_host(child)
            else:
                stat = child.stat()
                existing.size = 0 if child.is_dir() else stat.st_size
                existing.mtime = stat.st_mtime
                existing.atime = stat.st_atime
                existing.ctime = stat.st_ctime

    def _node_from_host(self, host: Path) -> Node:
        stat = host.stat()
        if host.is_dir():
            node = Node(host.name, "dir", self.user, self.group, 0o755, stat.st_ctime, stat.st_mtime, stat.st_atime)
            self._sync_dir(host, node)
            return node
        fid = uuid.uuid4().hex
        return Node(
            host.name,
            "file",
            self.user,
            self.group,
            0o644,
            stat.st_ctime,
            stat.st_mtime,
            stat.st_atime,
            stat.st_size,
            fid,
        )
