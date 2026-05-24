from __future__ import annotations

from mini_os.core.kernel import Kernel

COMMANDS = [
    "alias", "alloc", "append", "banker", "banner", "bg", "blocks", "bregister", "brelease", "brequest", "cat",
    "cd", "chmod", "chown", "clear", "cp", "create", "defrag", "df", "du", "echo", "env", "exit",
    "export", "find", "fg", "fragmap", "fsck", "grep", "help", "history", "jobs", "kill", "log",
    "ls", "man", "mkdir", "mkfs", "mount", "mutex", "mv", "nice", "pause", "ps", "pwd", "read", "realpath",
    "rm", "run", "sem", "spawn", "stat", "telemetry", "tick", "top", "topb", "touch", "tree", "umount",
    "version", "watch", "write", "quit", "logout", "fmexit",
    # external / third-party commands
    "python", "python3", "pip", "pip3", "curl", "wget", "git", "node", "npm", "npx", "deno",
    "docker", "ssh", "scp", "rsync", "tar", "zip", "unzip", "gzip", "gunzip", "make", "cmake",
    "cargo", "rustc", "go", "java", "javac", "ruby", "gem", "perl", "php", "which", "where",
]


class Completer:
    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel

    def candidates(self, words: list[str]) -> list[str]:
        if not words:
            return COMMANDS
        if len(words) == 1:
            return [c for c in COMMANDS if c.startswith(words[0])]
        cmd, prefix = words[0], words[-1]
        if cmd in {"alloc"}:
            return [m for m in ("contiguous", "linked", "indexed") if m.startswith(prefix)]
        if cmd in {"kill", "nice", "brequest", "brelease", "bregister"}:
            return [str(p.pid) for p in self.kernel.scheduler.table() if str(p.pid).startswith(prefix)]
        dirs_only = cmd in {"cd", "mkdir"}
        files_only = cmd in {"read", "cat", "grep", "stat", "chmod", "chown", "alloc"}
        return self.path_candidates(prefix, dirs_only=dirs_only, files_only=files_only)

    def path_candidates(self, prefix: str, dirs_only: bool = False, files_only: bool = False) -> list[str]:
        try:
            base = self.kernel.fs.norm(prefix or ".", self.kernel.cwd)
            parent_path = base if prefix.endswith("/") else "/".join(base.split("/")[:-1]) or "/"
            typed = "" if prefix.endswith("/") else base.split("/")[-1]
            parent = self.kernel.fs.resolve(parent_path)
        except Exception:
            return []
        out: list[str] = []
        for child in parent.children.values():
            if typed and not child.name.startswith(typed):
                continue
            if dirs_only and child.type != "dir":
                continue
            if files_only and child.type != "file":
                continue
            full = (parent_path.rstrip("/") + "/" + child.name) if parent_path != "/" else "/" + child.name
            out.append(full + ("/" if child.type == "dir" else ""))
        return out
