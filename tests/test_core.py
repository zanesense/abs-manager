from __future__ import annotations

from pathlib import Path

import pytest

from mini_os.cli.completion import Completer
from mini_os.cli.parser import parse_pipeline, split_chains
from mini_os.cli.shell import CommandRunner
from mini_os.core.disk import DiskManager
from mini_os.core.errors import PermissionDenied
from mini_os.core.filesystem import FileSystem
from mini_os.core.kernel import Kernel


def test_allocation_methods_round_trip(tmp_path: Path) -> None:
    disk = DiskManager(tmp_path / "disk.bin", num_blocks=12, block_size=4)
    disk.allocate("a", b"abcdefgh", "contiguous")
    assert disk.allocations["a"]["blocks"] == [0, 1]
    assert disk.read("a") == b"abcdefgh"
    disk.allocate("b", b"12345", "linked")
    assert disk.read("b") == b"12345"
    disk.reallocate("b", "indexed")
    assert disk.allocations["b"]["index_block"] is not None
    assert disk.read("b") == b"12345"


def test_filesystem_permissions_enforced(tmp_path: Path) -> None:
    disk = DiskManager(tmp_path / "disk.bin")
    fs = FileSystem(disk)
    fs.write("/secret", "shh")
    fs.chmod("/secret", 0o000)
    fs.user = "guest"
    with pytest.raises(PermissionDenied):
        fs.read("/secret")


def test_scheduler_aging_runs_low_priority_process(tmp_path: Path) -> None:
    kernel = Kernel(tmp_path)
    low = kernel.scheduler.spawn("low", 1, priority=20)
    high = kernel.scheduler.spawn("high", 6, priority=1)
    for _ in range(12):
        kernel.scheduler.tick_priority(1)
    assert low.state == "done"
    assert high.ticks > 0


def test_banker_denies_unsafe_request(tmp_path: Path) -> None:
    kernel = Kernel(tmp_path)
    banker = kernel.deadlock
    banker.register(1, [7, 5, 3])
    banker.register(2, [3, 2, 2])
    banker.register(3, [9, 0, 2])
    assert banker.request(1, [0, 1, 0])
    assert banker.request(2, [2, 0, 0])
    assert banker.request(3, [3, 0, 2])
    assert not banker.request(1, [6, 0, 0])


def test_journal_replay_and_persistence(tmp_path: Path) -> None:
    kernel = Kernel(tmp_path)
    runner = CommandRunner(kernel, color=False)
    assert runner.run_line("mkdir -p /var; write /var/a hello").code == 0
    assert "mkdir" in "\n".join(kernel.journal.replay())
    restarted = Kernel(tmp_path)
    assert restarted.fs.read("/var/a") == "hello"


def test_pipe_redirect_and_chaining(tmp_path: Path) -> None:
    kernel = Kernel(tmp_path)
    runner = CommandRunner(kernel, color=False)
    res = runner.run_line("write /a 'foo\nbar'; read /a | grep foo > /b && read /b")
    assert res.code == 0
    assert kernel.fs.read("/b") == "foo\n"
    assert (tmp_path / "b").read_text(encoding="utf-8") == "foo\n"


def test_parser_handles_shell_operators_without_spaces() -> None:
    assert split_chains("echo a;echo b&&echo c")[1][0] == ";"
    pipe = parse_pipeline("read /a|grep x>/b")
    assert pipe[0].argv == ["read", "/a"]
    assert pipe[1].argv == ["grep", "x"]
    assert pipe[1].redirect == "/b"


def test_completion_candidates(tmp_path: Path) -> None:
    kernel = Kernel(tmp_path)
    runner = CommandRunner(kernel, color=False)
    runner.run_line("mkdir -p /etc; write /etc/conf value; spawn p 2")
    comp = Completer(kernel)
    assert "/etc/" in comp.candidates(["cd", "/e"])
    assert "/etc/conf" in comp.candidates(["read", "/etc/c"])
    assert "indexed" in comp.candidates(["alloc", "i"])
