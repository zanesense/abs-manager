from __future__ import annotations

import argparse
import os
import subprocess

try:
    import readline
except ImportError:  # pragma: no cover - platform fallback
    readline = None  # type: ignore[assignment]
import shlex
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mini_os import APP_NAME, SHELL_HOST, __version__
from mini_os.cli.completion import Completer
from mini_os.cli.pager import page
from mini_os.cli.parser import parse_pipeline, split_chains
from mini_os.cli.render import Style, blocks, boot_screen, df, fragmap, ls_long, table, telemetry, tree
from mini_os.core.errors import MiniOSError, UsageError
from mini_os.core.kernel import Kernel


@dataclass
class Result:
    code: int = 0
    out: str = ""
    err: str = ""


class HelpFormatter(argparse.RawDescriptionHelpFormatter):
    pass


class CommandRunner:
    def __init__(self, kernel: Kernel, color: bool = True) -> None:
        self.kernel = kernel
        self.style = Style(color)
        self.aliases: dict[str, str] = {}
        self.env: dict[str, str] = {}
        self.jobs: dict[int, threading.Thread] = {}
        self.job_names: dict[int, str] = {}
        self._job_id = 1

    def run_line(self, line: str) -> Result:
        final = Result()
        for op, chunk in split_chains(line):
            if op == "&&" and final.code != 0:
                continue
            if op == "||" and final.code == 0:
                continue
            final = self.run_pipeline(chunk, op)
        return final

    def run_pipeline(self, line: str, op: str = ";") -> Result:
        try:
            segments = parse_pipeline(line, op)
        except ValueError as exc:
            return Result(2, err=str(exc))
        if not segments:
            return Result()
        if segments[-1].background:
            jid = self._job_id
            self._job_id += 1
            thread = threading.Thread(target=lambda: self.run_pipeline(line.replace("&", "").strip()), daemon=True)
            self.jobs[jid] = thread
            self.job_names[jid] = line
            thread.start()
            return Result(0, f"[{jid}] started\n")
        stdin = ""
        result = Result()
        for segment in segments:
            if not segment.argv:
                continue
            result = self.run_command(segment.argv, stdin)
            stdin = result.out
            if result.code != 0:
                break
        if result.code == 0 and segments[-1].redirect:
            target = segments[-1].redirect
            data = result.out
            mode = "a" if segments[-1].append else "w"
            if mode == "a":
                self.kernel.mutate(
                    "append",
                    {"path": target, "bytes": len(data)},
                    lambda: self.kernel.fs.write(target, data, self.kernel.cwd, append=True),
                )
            else:
                self.kernel.mutate(
                    "write",
                    {"path": target, "bytes": len(data)},
                    lambda: self.kernel.fs.write(target, data, self.kernel.cwd),
                )
            result.out = ""
        return result

    def run_command(self, argv: list[str], stdin: str = "") -> Result:
        if argv[0] in self.aliases:
            argv = shlex.split(self.aliases[argv[0]]) + argv[1:]
        name = argv[0]
        if len(argv) > 1 and argv[1] == "--help":
            return Result(0, self.man(name) + "\n")
        try:
            method = getattr(self, f"cmd_{name.replace('-', '_')}", None)
            if not method:
                return Result(127, err=f"{name}: command not found")
            return method(argv[1:], stdin)
        except SystemExit as exc:
            return Result(int(exc.code) if isinstance(exc.code, int) else 2)
        except MiniOSError as exc:
            return Result(exc.code, err=f"{name}: {exc}")
        except KeyboardInterrupt:
            return Result(130, err=f"{name}: interrupted")
        except EOFError:
            raise
        except Exception as exc:
            if os.environ.get("DEBUG") == "1":
                raise
            return Result(1, err=f"{name}: {exc}")

    def parser(self, prog: str, desc: str = "") -> argparse.ArgumentParser:
        return argparse.ArgumentParser(prog=prog, description=desc, formatter_class=HelpFormatter, add_help=True)

    def cmd_pwd(self, args: list[str], stdin: str) -> Result:
        return Result(0, self.kernel.cwd + "\n")

    def cmd_cd(self, args: list[str], stdin: str) -> Result:
        p = self.parser("cd", "change directory")
        p.add_argument("path", nargs="?", default="/")
        ns = p.parse_args(args)
        return Result(0, self.kernel.cd(ns.path) + "\n")

    def cmd_ls(self, args: list[str], stdin: str) -> Result:
        p = self.parser("ls", "list directory")
        p.add_argument("-l", action="store_true")
        p.add_argument("-a", action="store_true")
        p.add_argument("path", nargs="?", default=".")
        ns = p.parse_args(args)
        nodes = self.kernel.fs.listdir(ns.path, self.kernel.cwd, ns.a)
        if ns.l:
            return Result(0, ls_long(nodes, self.style) + "\n")
        names = [
            self.style.dir(n.name + "/") if n.type == "dir" and n.name not in {".", ".."} else n.name
            for n in nodes
        ]
        return Result(0, "  ".join(names) + ("\n" if names else ""))

    def cmd_mkdir(self, args: list[str], stdin: str) -> Result:
        p = self.parser("mkdir", "make directories")
        p.add_argument("-p", action="store_true")
        p.add_argument("paths", nargs="+")
        ns = p.parse_args(args)
        for path in ns.paths:
            self.kernel.mutate(
                "mkdir",
                {"path": path, "parents": ns.p},
                lambda pth=path: self.kernel.fs.mkdir(pth, self.kernel.cwd, ns.p),
            )
        return Result()

    def cmd_touch(self, args: list[str], stdin: str) -> Result:
        p = self.parser("touch", "create or update files")
        p.add_argument("paths", nargs="+")
        ns = p.parse_args(args)
        for path in ns.paths:
            self.kernel.mutate("touch", {"path": path}, lambda pth=path: self.kernel.fs.touch(pth, self.kernel.cwd))
        return Result()

    def cmd_create(self, args: list[str], stdin: str) -> Result:
        return self.cmd_touch(args, stdin)

    def cmd_write(self, args: list[str], stdin: str) -> Result:
        p = self.parser("write", "write text to file")
        p.add_argument("path")
        p.add_argument("text", nargs="*")
        ns = p.parse_args(args)
        data = " ".join(ns.text) if ns.text else stdin
        self.kernel.mutate(
            "write",
            {"path": ns.path, "bytes": len(data)},
            lambda: self.kernel.fs.write(ns.path, data, self.kernel.cwd),
        )
        return Result()

    def cmd_append(self, args: list[str], stdin: str) -> Result:
        p = self.parser("append", "append text to file")
        p.add_argument("path")
        p.add_argument("text", nargs="*")
        ns = p.parse_args(args)
        data = " ".join(ns.text) if ns.text else stdin
        self.kernel.mutate(
            "append",
            {"path": ns.path, "bytes": len(data)},
            lambda: self.kernel.fs.write(ns.path, data, self.kernel.cwd, append=True),
        )
        return Result()

    def cmd_read(self, args: list[str], stdin: str) -> Result:
        p = self.parser("read", "read file")
        p.add_argument("path")
        ns = p.parse_args(args)
        return Result(0, self.kernel.fs.read(ns.path, self.kernel.cwd))

    def cmd_cat(self, args: list[str], stdin: str) -> Result:
        return self.cmd_read(args, stdin) if args else Result(0, stdin)

    def cmd_cp(self, args: list[str], stdin: str) -> Result:
        p = self.parser("cp", "copy file")
        p.add_argument("src")
        p.add_argument("dst")
        ns = p.parse_args(args)
        self.kernel.mutate("cp", vars(ns), lambda: self.kernel.fs.copy(ns.src, ns.dst, self.kernel.cwd))
        return Result()

    def cmd_mv(self, args: list[str], stdin: str) -> Result:
        p = self.parser("mv", "move file")
        p.add_argument("src")
        p.add_argument("dst")
        ns = p.parse_args(args)
        self.kernel.mutate("mv", vars(ns), lambda: self.kernel.fs.move(ns.src, ns.dst, self.kernel.cwd))
        return Result()

    def cmd_rm(self, args: list[str], stdin: str) -> Result:
        p = self.parser("rm", "remove files")
        p.add_argument("-r", action="store_true")
        p.add_argument("paths", nargs="+")
        ns = p.parse_args(args)
        for path in ns.paths:
            self.kernel.mutate(
                "rm",
                {"path": path, "recursive": ns.r},
                lambda pth=path: self.kernel.fs.remove(pth, self.kernel.cwd, ns.r),
            )
        return Result()

    def cmd_stat(self, args: list[str], stdin: str) -> Result:
        p = self.parser("stat", "show file metadata")
        p.add_argument("path")
        ns = p.parse_args(args)
        node = self.kernel.fs.resolve(ns.path, self.kernel.cwd)
        rows = [
            ["path", self.kernel.fs.norm(ns.path, self.kernel.cwd)],
            ["type", node.type],
            ["mode", oct(node.mode)],
            ["owner", node.owner],
            ["group", node.group],
            ["size", node.size],
            ["file_id", node.file_id or ""],
        ]
        return Result(0, table(["field", "value"], rows) + "\n")

    def cmd_realpath(self, args: list[str], stdin: str) -> Result:
        p = self.parser("realpath", "show host filesystem path")
        p.add_argument("path", nargs="?", default=".")
        ns = p.parse_args(args)
        return Result(0, str(self.kernel.fs.host_path(ns.path, self.kernel.cwd)) + "\n")

    def cmd_find(self, args: list[str], stdin: str) -> Result:
        p = self.parser("find", "find paths by pattern")
        p.add_argument("pattern")
        p.add_argument("path", nargs="?", default="/")
        ns = p.parse_args(args)
        return Result(0, "\n".join(self.kernel.fs.find(ns.pattern, ns.path, self.kernel.cwd)) + "\n")

    def cmd_grep(self, args: list[str], stdin: str) -> Result:
        p = self.parser("grep", "search text")
        p.add_argument("pattern")
        p.add_argument("path", nargs="?")
        ns = p.parse_args(args)
        text = self.kernel.fs.read(ns.path, self.kernel.cwd) if ns.path else stdin
        return Result(0, "".join(line + "\n" for line in text.splitlines() if ns.pattern in line))

    def cmd_tree(self, args: list[str], stdin: str) -> Result:
        return Result(0, tree(self.kernel, args[0] if args else "/") + "\n")

    def cmd_chmod(self, args: list[str], stdin: str) -> Result:
        p = self.parser("chmod", "change mode")
        p.add_argument("mode")
        p.add_argument("path")
        ns = p.parse_args(args)
        self.kernel.mutate("chmod", vars(ns), lambda: self.kernel.fs.chmod(ns.path, int(ns.mode, 8), self.kernel.cwd))
        return Result()

    def cmd_chown(self, args: list[str], stdin: str) -> Result:
        p = self.parser("chown", "change owner")
        p.add_argument("owner_group")
        p.add_argument("path")
        ns = p.parse_args(args)
        owner, _, group = ns.owner_group.partition(":")
        self.kernel.mutate(
            "chown",
            vars(ns),
            lambda: self.kernel.fs.chown(ns.path, owner, group or None, self.kernel.cwd),
        )
        return Result()

    def cmd_du(self, args: list[str], stdin: str) -> Result:
        path = args[0] if args else "/"
        rows = [[p, n.size] for p, n in self.kernel.fs.walk(path, self.kernel.cwd)]
        return Result(0, table(["path", "bytes"], rows) + "\n")

    def cmd_df(self, args: list[str], stdin: str) -> Result:
        return Result(0, df(self.kernel, self.style) + "\n")

    def cmd_blocks(self, args: list[str], stdin: str) -> Result:
        return Result(0, blocks(self.kernel, self.style) + "\n")

    def cmd_alloc(self, args: list[str], stdin: str) -> Result:
        p = self.parser("alloc", "change file allocation method")
        p.add_argument("path")
        p.add_argument("method", choices=["contiguous", "linked", "indexed"])
        ns = p.parse_args(args)
        self.kernel.alloc(ns.path, ns.method)
        return Result()

    def cmd_defrag(self, args: list[str], stdin: str) -> Result:
        self.kernel.mutate("defrag", {}, self.kernel.disk.compact)
        return Result(0, "disk compacted\n")

    def cmd_fsck(self, args: list[str], stdin: str) -> Result:
        ok, errors = self.kernel.disk.fsck()
        return Result(0 if ok else 1, "fsck ok\n" if ok else "\n".join(errors) + "\n")

    def cmd_mkfs(self, args: list[str], stdin: str) -> Result:
        p = self.parser("mkfs", "format disk")
        p.add_argument("--yes", "-y", action="store_true")
        p.add_argument("--blocks", type=int, default=256)
        p.add_argument("--block-size", type=int, default=256)
        ns = p.parse_args(args)
        if not ns.yes and sys.stdin.isatty():
            if input("mkfs destroys all data. Type yes: ") != "yes":
                return Result(1, err="mkfs: cancelled")
        self.kernel.mkfs(ns.blocks, ns.block_size)
        return Result(0, "formatted\n")

    def cmd_mount(self, args: list[str], stdin: str) -> Result:
        self.kernel.disk.mount()
        self.kernel.load()
        return Result(0, "mounted\n")

    def cmd_umount(self, args: list[str], stdin: str) -> Result:
        self.kernel.flush()
        self.kernel.disk.umount()
        return Result(0, "unmounted\n")

    def cmd_spawn(self, args: list[str], stdin: str) -> Result:
        p = self.parser("spawn", "create process")
        p.add_argument("name")
        p.add_argument("burst", type=int)
        p.add_argument("prio", type=int, nargs="?", default=5)
        ns = p.parse_args(args)
        proc = self.kernel.scheduler.spawn(ns.name, ns.burst, ns.prio)
        return Result(0, f"{proc.pid}\n")

    def cmd_ps(self, args: list[str], stdin: str) -> Result:
        rows = [
            [p.pid, p.name, p.state, p.priority, p.remaining, p.ticks, p.age]
            for p in self.kernel.scheduler.table()
        ]
        return Result(0, table(["pid", "name", "state", "prio", "remain", "ticks", "age"], rows) + "\n")

    def cmd_kill(self, args: list[str], stdin: str) -> Result:
        if not args:
            raise UsageError("kill: missing pid")
        return Result(0 if self.kernel.scheduler.kill(int(args[0])) else 1)

    def cmd_nice(self, args: list[str], stdin: str) -> Result:
        if len(args) != 2:
            raise UsageError("nice: pid priority")
        self.kernel.scheduler.nice(int(args[0]), int(args[1]))
        return Result()

    def cmd_tick(self, args: list[str], stdin: str) -> Result:
        n = int(args[0]) if args else 1
        algo = args[1] if len(args) > 1 else "rr"
        ran = self.kernel.scheduler.tick_priority(n) if algo.startswith("prio") else self.kernel.scheduler.tick_rr(n)
        lines = "\n".join(f"pid {p.pid} {p.state} remaining={p.remaining}" for p in ran)
        return Result(0, lines + ("\n" if ran else ""))

    def cmd_run(self, args: list[str], stdin: str) -> Result:
        self.kernel.scheduler.start()
        return Result(0, "scheduler running\n")

    def cmd_pause(self, args: list[str], stdin: str) -> Result:
        self.kernel.scheduler.pause()
        return Result(0, "scheduler paused\n")

    def cmd_top(self, args: list[str], stdin: str) -> Result:
        return self.cmd_ps(args, stdin)

    def cmd_jobs(self, args: list[str], stdin: str) -> Result:
        rows = [[jid, "running" if t.is_alive() else "done", self.job_names[jid]] for jid, t in self.jobs.items()]
        return Result(0, table(["job", "state", "command"], rows) + "\n")

    def cmd_fg(self, args: list[str], stdin: str) -> Result:
        jid = int(args[0]) if args else max(self.jobs, default=0)
        if jid in self.jobs:
            self.jobs[jid].join()
        return Result()

    def cmd_bg(self, args: list[str], stdin: str) -> Result:
        return self.cmd_jobs(args, stdin)

    def _res(self, text: str) -> list[int]:
        return [int(x) for x in text.split(",") if x != ""]

    def cmd_banker(self, args: list[str], stdin: str) -> Result:
        rows = [[r["pid"], r["max"], r["alloc"], r["need"]] for r in self.kernel.deadlock.table()]
        out = table(["pid", "max", "alloc", "need"], rows)
        out += f"\navailable={self.kernel.deadlock.available()} safe={self.kernel.deadlock.is_safe()}\n"
        return Result(0, out)

    def cmd_bregister(self, args: list[str], stdin: str) -> Result:
        self.kernel.deadlock.register(int(args[0]), self._res(args[1]))
        return Result()

    def cmd_brequest(self, args: list[str], stdin: str) -> Result:
        ok = self.kernel.deadlock.request(int(args[0]), self._res(args[1]))
        return Result(0 if ok else 1, "granted\n" if ok else "denied unsafe\n")

    def cmd_brelease(self, args: list[str], stdin: str) -> Result:
        self.kernel.deadlock.release(int(args[0]), self._res(args[1]))
        return Result()

    def cmd_sem(self, args: list[str], stdin: str) -> Result:
        return Result(0, self.kernel.sem(args[0], int(args[1])) + "\n")

    def cmd_mutex(self, args: list[str], stdin: str) -> Result:
        return Result(0, self.kernel.mutex(args[0], args[1]) + "\n")

    def cmd_log(self, args: list[str], stdin: str) -> Result:
        if args and args[0] == "replay":
            return Result(0, "\n".join(self.kernel.journal.replay()) + "\n")
        if args and args[0] == "clear":
            self.kernel.journal.clear()
            return Result(0, "journal cleared\n")
        n = int(args[0]) if args else 20
        return Result(0, "\n".join(f"{e['op']} {e['payload']}" for e in self.kernel.journal.tail(n)) + "\n")

    def cmd_journal(self, args: list[str], stdin: str) -> Result:
        ok, msg = self.kernel.journal.verify()
        return Result(0 if ok else 1, msg + "\n")

    def cmd_history(self, args: list[str], stdin: str) -> Result:
        if readline is None:
            return Result(0, "")
        rows = [readline.get_history_item(i) or "" for i in range(1, readline.get_current_history_length() + 1)]
        return Result(0, "\n".join(rows) + "\n")

    def cmd_alias(self, args: list[str], stdin: str) -> Result:
        if not args:
            return Result(0, "\n".join(f"{k}='{v}'" for k, v in self.aliases.items()) + "\n")
        name, _, value = " ".join(args).partition("=")
        self.aliases[name.strip()] = value.strip().strip("'\"")
        return Result()

    def cmd_export(self, args: list[str], stdin: str) -> Result:
        for item in args:
            k, _, v = item.partition("=")
            self.env[k] = v
            os.environ[k] = v
        return Result()

    def cmd_env(self, args: list[str], stdin: str) -> Result:
        merged = dict(os.environ) | self.env
        return Result(0, "\n".join(f"{k}={v}" for k, v in sorted(merged.items())) + "\n")

    def cmd_echo(self, args: list[str], stdin: str) -> Result:
        return Result(0, " ".join(args) + "\n")

    def cmd_clear(self, args: list[str], stdin: str) -> Result:
        command = "cls" if os.name == "nt" else "clear"
        try:
            subprocess.run(command, shell=True, check=False)
        except Exception:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
        return Result()

    def cmd_help(self, args: list[str], stdin: str) -> Result:
        if args:
            return Result(0, self.man(args[0]) + "\n")
        groups = {
            "Filesystem": (
                "ls cd pwd mkdir touch create write append read cat cp mv rm stat find grep tree chmod chown du df "
                "realpath"
            ),
            "Disk": "blocks alloc defrag fsck mkfs mount umount fragmap topb",
            "Processes": "spawn ps kill nice tick run pause top jobs fg bg",
            "Sync/Deadlock": "banker bregister brequest brelease sem mutex",
            "System": (
                "banner telemetry log journal history alias export env echo clear help man version "
                "exit quit logout fmexit watch"
            ),
        }
        return Result(0, "\n".join(f"{self.style.header(g)}: {cmds}" for g, cmds in groups.items()) + "\n")

    def cmd_man(self, args: list[str], stdin: str) -> Result:
        if not args:
            raise UsageError("man: missing command")
        return Result(0, self.man(args[0]) + "\n")

    def man(self, cmd: str) -> str:
        synopsis = {
            "ls": "ls [-l|-a] [path]", "mkdir": "mkdir -p path", "write": "write path text", "grep": "grep pat [path]",
            "alloc": "alloc path contiguous|linked|indexed",
            "spawn": "spawn name burst [prio]",
            "tick": "tick [n] [rr|priority]",
            "brequest": "brequest pid a,b,c",
            "mkfs": "mkfs -y [--blocks N] [--block-size N]",
            "exit": "exit",
            "quit": "quit",
            "logout": "logout",
            "fmexit": "fmexit",
            "banner": "banner",
            "telemetry": "telemetry",
            "realpath": "realpath [path]",
        }.get(cmd, f"{cmd} [args]")
        return (
            f"{cmd}\n  synopsis: {synopsis}\n  examples: {synopsis}\n"
            "  exit codes: 0 ok, 1 error, 2 usage, 13 permission, 17 exists, 28 nospace, 39 not-empty"
        )

    def cmd_version(self, args: list[str], stdin: str) -> Result:
        return Result(0, f"{APP_NAME} {__version__} Python {sys.version.split()[0]}\n")

    def cmd_banner(self, args: list[str], stdin: str) -> Result:
        return Result(0, boot_screen(self.kernel, self.style) + "\n")

    def cmd_telemetry(self, args: list[str], stdin: str) -> Result:
        return Result(0, telemetry(self.kernel, self.style) + "\n")

    def cmd_exit(self, args: list[str], stdin: str) -> Result:
        self.kernel.flush()
        raise EOFError

    def cmd_quit(self, args: list[str], stdin: str) -> Result:
        return self.cmd_exit(args, stdin)

    def cmd_logout(self, args: list[str], stdin: str) -> Result:
        return self.cmd_exit(args, stdin)

    def cmd_fmexit(self, args: list[str], stdin: str) -> Result:
        return self.cmd_exit(args, stdin)

    def cmd_watch(self, args: list[str], stdin: str) -> Result:
        interval = float(args[-1]) if args and args[-1].replace(".", "", 1).isdigit() else 1.0
        cmd = args[:-1] if args and args[-1].replace(".", "", 1).isdigit() else args
        if not cmd:
            raise UsageError("watch: missing command")
        for _ in range(3 if not sys.stdout.isatty() else 10**9):
            res = self.run_command(cmd)
            sys.stdout.write("\033[H\033[2J" + res.out)
            sys.stdout.flush()
            time.sleep(interval)
        return Result()

    def cmd_topb(self, args: list[str], stdin: str) -> Result:
        return self.cmd_blocks(args, stdin)

    def cmd_fragmap(self, args: list[str], stdin: str) -> Result:
        return Result(0, fragmap(self.kernel) + "\n")


class Shell:
    def __init__(self, kernel: Kernel, interactive: bool = True, color: bool = True) -> None:
        self.kernel = kernel
        self.runner = CommandRunner(kernel, color=color)
        self.interactive = interactive
        self.last = 0
        self.history = kernel.root_path / ".absmanager_history"
        self.completer = Completer(kernel)

    def setup_readline(self) -> None:
        try:
            if readline is None:
                return
            self.history.touch(exist_ok=True)
            readline.read_history_file(self.history)
            readline.set_history_length(2000)
            readline.parse_and_bind("tab: complete")
            readline.set_completer(self._complete)
        except Exception:
            pass

    def _complete(self, text: str, state: int) -> str | None:
        line = readline.get_line_buffer()
        words = shlex.split(line[: readline.get_endidx()] or text)
        if line.endswith(" "):
            words.append("")
        cands = self.completer.candidates(words)
        return cands[state] if state < len(cands) else None

    def prompt(self) -> str:
        style = self.runner.style
        if not style.enabled:
            sign = "$" if self.last == 0 else "!"
            return f"{self.kernel.user}@{SHELL_HOST}:{self.kernel.cwd}{sign} "
        cwd = self._prompt_cwd()
        parts: list[str] = []
        if self.last:
            parts.append(style.segment(f"x {self.last}", 37, 41, 100))
            parts.append(style.segment(f"{self.kernel.user}@{SHELL_HOST}", 37, 100, 44))
        else:
            parts.append(style.segment(f"{self.kernel.user}@{SHELL_HOST}", 37, 100, 44))
        parts.append(style.segment(cwd, 37, 44, 46))
        parts.append(style.segment("ABS", 30, 46, None))
        return "".join(parts) + style.fg(" \u276f ", 36, True)

    def _prompt_cwd(self) -> str:
        if self.kernel.cwd == "/":
            return "/"
        parts = [part for part in self.kernel.cwd.strip("/").split("/") if part]
        if len(parts) <= 3:
            return "/" + "/".join(parts)
        return ".../" + "/".join(parts[-2:])

    def loop(self) -> int:
        self.setup_readline()
        signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        if self.interactive:
            print(boot_screen(self.kernel, self.runner.style))
        try:
            while True:
                try:
                    line = input(self.prompt())
                except KeyboardInterrupt:
                    print("^C")
                    self.last = 130
                    continue
                except EOFError:
                    print()
                    break
                try:
                    res = self.runner.run_line(line)
                except EOFError:
                    print()
                    break
                self.last = res.code
                self.emit(res)
        finally:
            try:
                if readline is not None:
                    readline.write_history_file(self.history)
            except Exception:
                pass
            self.kernel.flush()
        return self.last

    def emit(self, res: Result) -> None:
        if res.out:
            page(res.out.rstrip("\n"), enabled=self.interactive)
        if res.err:
            print(res.err, file=sys.stderr)

    def run_script(self, text: str) -> int:
        code = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                res = self.runner.run_line(line)
            except EOFError:
                break
            code = res.code
            self.emit(res)
        self.kernel.flush()
        return code


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kernel = Kernel(Path.cwd())
    interactive = sys.stdin.isatty() and not argv
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    shell = Shell(kernel, interactive=interactive, color=color)
    if argv[:1] == ["-c"]:
        return shell.run_script(argv[1] if len(argv) > 1 else "")
    if argv:
        return shell.run_script(Path(argv[0]).read_text(encoding="utf-8"))
    if not sys.stdin.isatty():
        return shell.run_script(sys.stdin.read())
    return shell.loop()
