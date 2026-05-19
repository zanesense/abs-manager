from __future__ import annotations

import os
import platform
import re
import shutil
import sys
import time
from typing import Any, Iterable

from mini_os import APP_NAME, __version__
from mini_os.core.filesystem import Node
from mini_os.core.kernel import Kernel

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class Style:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled and "NO_COLOR" not in os.environ

    def c(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def fg(self, text: str, color: int, bold: bool = False) -> str:
        return self.c(text, f"{color}{';1' if bold else ''}")

    def bg(self, text: str, fg: int, bg: int, bold: bool = False) -> str:
        return self.c(text, f"{fg};{bg}{';1' if bold else ''}")

    def dir(self, text: str) -> str:
        return self.fg(text, 94, True)

    def file(self, text: str) -> str:
        return self.fg(text, 37)

    def header(self, text: str) -> str:
        return self.fg(text, 94, True)

    def err(self, text: str) -> str:
        return self.c(text, "31;1")

    def ok(self, text: str) -> str:
        return self.fg(text, 36, True)

    def warn(self, text: str) -> str:
        return self.fg(text, 33, True)

    def cyan(self, text: str) -> str:
        return self.fg(text, 36, True)

    def blue(self, text: str) -> str:
        return self.fg(text, 94, True)

    def navy(self, text: str) -> str:
        return self.fg(text, 34, True)

    def strong(self, text: str) -> str:
        return self.fg(text, 37, True)

    def dim(self, text: str) -> str:
        return self.c(text, "2")

    def segment(self, text: str, fg: int, bg: int, next_bg: int | None = None, bold: bool = True) -> str:
        if not self.enabled:
            return f" {text} "
        sep = powerline_separator()
        body = self.bg(f" {text} ", fg, bg, bold)
        sep_fg = 30 + (bg - 40)
        if next_bg is None:
            return body + self.fg(sep, sep_fg)
        return body + self.c(sep, f"{sep_fg};{next_bg}")


def powerline_separator() -> str:
    sep = "\ue0b0"
    try:
        sep.encode(sys.stdout.encoding or "utf-8")
    except UnicodeEncodeError:
        return ">"
    return sep


def banner(style: Style) -> str:
    art = [
        "                                                                 ",
        "                                                                 ",
        " ▄▄▄  ▄▄▄▄   ▄▄▄▄ ▄▄   ▄▄  ▄▄▄  ▄▄  ▄▄  ▄▄▄   ▄▄▄▄ ▄▄▄▄▄ ▄▄▄▄    ",
        "██▀██ ██▄██ ███▄▄ ██▀▄▀██ ██▀██ ███▄██ ██▀██ ██ ▄▄ ██▄▄  ██▄█▄   ",
        "██▀██ ██▄█▀ ▄▄██▀ ██   ██ ██▀██ ██ ▀██ ██▀██ ▀███▀ ██▄▄▄ ██ ██ ▄ ",
        "                                                                 ",
    ]
    glow = [style.blue(line) if i % 2 == 0 else style.cyan(line) for i, line in enumerate(art)]
    subtitle = style.cyan(f"{APP_NAME} v{__version__}") + style.dim(" :: virtual file system control deck")
    return "\n".join(glow + [subtitle, ""])


def telemetry(kernel: Kernel, style: Style) -> str:
    used = kernel.disk.used_blocks()
    total = kernel.disk.num_blocks
    free = total - used
    capacity = total * kernel.disk.block_size
    used_bytes = used * kernel.disk.block_size
    logical_bytes = sum(node.size for _, node in kernel.fs.walk("/") if node.type == "file")
    frag = kernel.disk.fragmentation()
    nodes = kernel.fs.walk("/")
    file_count = sum(1 for _, node in nodes if node.type == "file")
    dir_count = sum(1 for _, node in nodes if node.type == "dir")
    procs = kernel.scheduler.table()
    runnable = sum(1 for proc in procs if proc.state in {"ready", "running"})
    done = sum(1 for proc in procs if proc.state == "done")
    killed = sum(1 for proc in procs if proc.state == "killed")
    journal_entries = len(kernel.journal.entries())
    journal_ok, journal_msg = kernel.journal.verify()
    fsck_ok, fsck_errors = kernel.disk.fsck()
    alloc_methods = {"contiguous": 0, "linked": 0, "indexed": 0}
    index_blocks = 0
    for meta in kernel.disk.allocations.values():
        method = str(meta.get("method", "indexed"))
        alloc_methods[method] = alloc_methods.get(method, 0) + 1
        index_blocks += 1 if meta.get("index_block") is not None else 0
    largest_files = sorted(
        ((path, node.size) for path, node in nodes if node.type == "file"),
        key=lambda item: item[1],
        reverse=True,
    )[:5]
    disk_image_size = kernel.disk.disk_path.stat().st_size if kernel.disk.disk_path.exists() else 0
    block_bar = progress(used, total, 32)
    if style.enabled:
        filled = round(32 * used / max(total, 1))
        block_bar = style.ok("#" * filled) + style.dim("." * (32 - filled))

    disk_rows = [
        ["capacity", human(capacity), "disk image", human(disk_image_size)],
        ["used", human(used_bytes), f"{used}/{total} blocks", f"{used * 100 / max(total, 1):.1f}%"],
        ["free", human(capacity - used_bytes), f"{free} empty blocks", block_bar],
        ["block size", f"{kernel.disk.block_size}B", "bitmap", f"{len(kernel.disk.bitmap)} bits"],
        ["logical data", human(logical_bytes), "slack/overhead", human(max(used_bytes - logical_bytes, 0))],
    ]
    alloc_rows = [
        ["contiguous", alloc_methods.get("contiguous", 0), "files", ""],
        ["linked", alloc_methods.get("linked", 0), "files", ""],
        ["indexed", alloc_methods.get("indexed", 0), "files", f"{index_blocks} index blocks"],
        ["fragmentation", f"{frag['fragmentation_percent']}%", "largest free run", frag["largest_free_run"]],
        ["extents", frag["extents"], "free runs", len(frag["free_runs"])],
    ]
    fs_rows = [
        ["cwd", kernel.cwd, "user", kernel.user],
        ["nodes", len(nodes), "files/dirs", f"{file_count}/{dir_count}"],
        ["largest files", "", "", ""],
    ]
    fs_rows.extend([[path, human(size), "logical bytes", size] for path, size in largest_files])
    proc_rows = [
        ["processes", len(procs), "runnable", runnable],
        ["done", done, "killed", killed],
        ["quantum", kernel.scheduler.quantum, "history ticks", len(kernel.scheduler.history)],
        ["scheduler thread", "active" if kernel.scheduler._running.is_set() else "paused", "mode", "rr/priority"],
    ]
    banker_rows = [
        ["total", kernel.deadlock.total, "available", kernel.deadlock.available()],
        ["registered", len(kernel.deadlock.maximum), "safe", kernel.deadlock.is_safe()],
    ]
    sync_rows = [
        ["mutexes", len(kernel.mutexes), "semaphores", len(kernel.semaphores)],
        ["io pool", "semaphore-limited", "journal", f"{journal_entries} entries"],
        ["journal verify", "ok" if journal_ok else "bad", "detail", journal_msg],
        ["fsck", "ok" if fsck_ok else "bad", "errors", len(fsck_errors)],
    ]
    runtime_rows = [
        ["app", f"{APP_NAME} {__version__}", "python", sys.version.split()[0]],
        ["host root", str(kernel.root_path), "mode", "windows files"],
        ["platform", platform.system() or "unknown", "release", platform.release() or "unknown"],
        ["color", "on" if style.enabled else "off", "terminal", shutil.get_terminal_size((80, 24)).columns],
    ]
    sections = [
        style.header("telemetry :: disk"),
        table(["metric", "value", "detail", "signal"], disk_rows),
        "",
        style.header("allocation + fragmentation"),
        table(["metric", "value", "detail", "signal"], alloc_rows),
        "",
        style.header("filesystem"),
        table(["metric", "value", "detail", "signal"], fs_rows),
        "",
        style.header("process scheduler"),
        table(["metric", "value", "detail", "signal"], proc_rows),
        "",
        style.header("deadlock + synchronization"),
        table(["metric", "value", "detail", "signal"], banker_rows + sync_rows),
        "",
        style.header("runtime"),
        table(["metric", "value", "detail", "signal"], runtime_rows),
        "",
    ]
    return "\n".join(sections)


def boot_screen(kernel: Kernel, style: Style) -> str:
    return banner(style)


def human(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{num_bytes}B"


def table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    rows_s = [[str(x) for x in row] for row in rows]
    widths = [visible_len(h) for h in headers]
    for row in rows_s:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], visible_len(cell))
    line = "  ".join(pad(h, widths[idx]) for idx, h in enumerate(headers))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(pad(cell, widths[idx]) for idx, cell in enumerate(row)) for row in rows_s]
    return "\n".join([line, sep, *body]) if body else "\n".join([line, sep])


def visible_len(text: str) -> int:
    return len(ANSI_RE.sub("", text))


def pad(text: str, width: int) -> str:
    return text + (" " * max(width - visible_len(text), 0))


def mode_str(mode: int, typ: str) -> str:
    bits = "rwx"
    out = "d" if typ == "dir" else "-"
    for shift in (6, 3, 0):
        val = (mode >> shift) & 7
        out += "".join(bits[i] if val & (4 >> i) else "-" for i in range(3))
    return out


def ls_long(nodes: list[Node], style: Style) -> str:
    rows = []
    for node in nodes:
        name = style.dir(node.name + "/") if node.type == "dir" and node.name not in {".", ".."} else node.name
        if node.type == "file":
            name = style.file(name)
        modified = time.strftime("%Y-%m-%d %H:%M", time.localtime(node.mtime))
        rows.append([style.ok(mode_str(node.mode, node.type)), node.owner, node.group, node.size, modified, name])
    return table(["mode", "owner", "group", "size", "modified", "name"], rows)


def progress(used: int, total: int, width: int = 24) -> str:
    total = max(total, 1)
    filled = round(width * used / total)
    return "#" * filled + "." * (width - filled)


def df(kernel: Kernel, style: Style) -> str:
    used = kernel.disk.used_blocks()
    total = kernel.disk.num_blocks
    percent = used * 100 / total
    bar = progress(used, total)
    if style.enabled:
        filled = round(24 * used / max(total, 1))
        bar = style.ok("#" * filled) + style.dim("." * (24 - filled))
    rows = [[used, total, f"{percent:.1f}%", bar]]
    out = [
        style.header("disk usage"),
        table(["used", "total", "use%", "blocks"], rows),
        "",
        style.header("files:"),
    ]
    files = []
    for path, node in kernel.fs.walk("/"):
        if node.type == "file":
            meta = kernel.disk.allocations.get(node.file_id or "", {})
            files.append([path, node.size, meta.get("method", "?"), ",".join(map(str, meta.get("blocks", [])))])
    out.append(table(["path", "bytes", "alloc", "blocks"], files))
    return "\n".join(out)


def blocks(kernel: Kernel, style: Style) -> str:
    chars = ["."] * kernel.disk.num_blocks
    legend: list[str] = [". free"]
    palette = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    colors = [94, 36, 34, 37, 90]
    for idx, (fid, meta) in enumerate(kernel.disk.allocations.items()):
        char = palette[idx % len(palette)]
        label = style.fg(char, colors[idx % len(colors)], True)
        legend.append(f"{label} {fid[:8]} {meta['method']}")
        for block in meta.get("blocks", []):
            chars[block] = "i" if meta.get("index_block") == block else char
    width = min(64, max(16, shutil.get_terminal_size((80, 20)).columns - 4))
    if style.enabled:
        colored = []
        for ch in chars:
            if ch == ".":
                colored.append(style.dim("."))
            elif ch == "i":
                colored.append(style.warn("i"))
            else:
                colored.append(style.fg(ch, colors[palette.index(ch) % len(colors)], True))
        chars = colored
    grid = ["".join(chars[i : i + width]) for i in range(0, len(chars), width)]
    return "\n".join([style.header("block map"), *grid, "", style.header("legend:"), *legend])


def tree(kernel: Kernel, path: str = "/") -> str:
    root = kernel.fs.resolve(path, kernel.cwd)
    lines = [f"{root.name if path == '/' else kernel.fs.norm(path, kernel.cwd)} ({root.size}B)"]
    def rec(node: Node, prefix: str) -> None:
        items = sorted(node.children.values(), key=lambda x: (x.type != "dir", x.name))
        for idx, child in enumerate(items):
            last = idx == len(items) - 1
            mark = "└── " if last else "├── "
            suffix = "/" if child.type == "dir" else f" ({child.size}B)"
            lines.append(prefix + mark + child.name + suffix)
            if child.type == "dir":
                rec(child, prefix + ("    " if last else "│   "))
    if root.type == "dir":
        rec(root, "")
    return "\n".join(lines)


def fragmap(kernel: Kernel) -> str:
    info = kernel.disk.fragmentation()
    bar = "".join("#" if used else "." for used in kernel.disk.bitmap)
    return (
        f"{bar}\nfragmentation={info['fragmentation_percent']}% "
        f"largest_free_run={info['largest_free_run']} extents={info['extents']}"
    )
