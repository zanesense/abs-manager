<div style="text-align: center;">
  <img src="https://i.postimg.cc/BtfXzby8/Untitled-(3).png" alt="bnr">
</div>

# ABS Manager - Multiplatform File Manager purely in Python.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Interface](https://img.shields.io/badge/interface-CLI%20only-111111?style=for-the-badge&logo=gnometerminal&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows%20friendly-0078D4?style=for-the-badge&logo=windows&logoColor=white)
![Status](https://img.shields.io/badge/status-teaching%20OS-7C3AED?style=for-the-badge)
![No GUI](https://img.shields.io/badge/GUI-none-000000?style=for-the-badge)

**ABS Manager** is a terminal-only teaching operating system and Windows-backed file manager. It creates normal files and folders in your current directory while simulating OS internals such as block allocation, permissions, process scheduling, deadlock avoidance, journaling, and disk visualization.

No GUI. No web server. No HTTP API. The shell is the product.

## Why It Exists

ABS Manager is built for learning by doing. Instead of reading about filesystem trees, block allocation, schedulers, deadlock safety checks, and journals in isolation, you can run commands and watch those systems change in one shell.

It is also practical enough to behave like a tiny file manager: paths such as `/docs/a.txt` map to real files under the folder where you launched ABS Manager.

## Highlights

- **Host-backed files**: create normal Windows files/folders with ABS shell commands.
- **Simulated disk**: fixed-size `disk.bin`, block bitmap, allocation metadata, `fsck`, and compaction.
- **Allocation strategies**: contiguous, linked, and indexed allocation per file.
- **Filesystem metadata**: owner, group, Unix-style modes, timestamps, size, and file ids.
- **Scheduling**: Round Robin plus priority scheduling with aging.
- **Deadlock avoidance**: Banker's Algorithm with safe/unsafe request handling.
- **Journaling**: append-only log, replay, tail, clear, and verification.
- **Shell UX**: banner, colored agnoster-style prompt, history, completion, pipes, redirects, chaining, scripting.
- **Visualizations**: `blocks`, `df`, `tree`, `fragmap`, `topb`, and full `telemetry`.

## Quickstart

```powershell
python main.py
```

Run a one-liner:

```powershell
python main.py -c "mkdir -p /docs; write /docs/a.txt hello; tree /"
```

Run the demo:

```powershell
python main.py demo.mosh
```

Exit cleanly:

```sh
exit
```

`quit`, `logout`, and `fmexit` work too.

## First Commands

```sh
mkdir -p /docs
write /docs/a.txt hello
append /docs/a.txt " from ABS Manager"
read /docs/a.txt
realpath /docs/a.txt
tree /
df
blocks
telemetry
```

Because the filesystem is host-backed, `/docs/a.txt` becomes:

```text
<launch-directory>\docs\a.txt
```

## Storage Model

ABS Manager operates in the directory where you launch it.

| Path | Purpose |
| --- | --- |
| Normal files/folders | User-created content from commands such as `mkdir`, `touch`, `write`, `cp`, and `mv` |
| `disk.bin` | Fixed-size simulated block disk |
| `fs_state.json` | Persisted metadata, tree state, bitmap, and allocation records |
| `journal.log` | Append-only log of mutating operations |
| `.absmanager_history` | Shell command history for the current working directory |

> `rm` and `rm -r` delete real files inside the launch directory. ABS Manager blocks path traversal outside that directory, but destructive commands are still real inside it.

## Shell Features

ABS Manager supports common shell workflows:

```sh
read /docs/a.txt | grep ABS > /matches.txt
mkdir /tmp && write /tmp/status ok || echo failed
spawn worker 5 3
tick 4 priority
watch ps 1
```

It also supports:

- Interactive history through `readline` when available.
- Command, path, PID, and allocation-method completion.
- `;`, `&&`, `||` command chaining.
- Pipes and output redirection.
- Background jobs with `&`, `jobs`, `fg`, and `bg`.
- Script files with `.mosh`.
- POSIX-like exit codes.

## Command Reference

Run `help`, `help <cmd>`, `man <cmd>`, or `<cmd> --help` inside the shell.

| Category | Commands |
| --- | --- |
| Filesystem | `ls`, `cd`, `pwd`, `mkdir`, `touch`, `create`, `write`, `append`, `read`, `cat`, `cp`, `mv`, `rm`, `stat`, `find`, `grep`, `tree`, `chmod`, `chown`, `du`, `df`, `realpath` |
| Disk | `blocks`, `alloc`, `defrag`, `fsck`, `mkfs`, `mount`, `umount`, `topb`, `fragmap` |
| Processes | `spawn`, `ps`, `kill`, `nice`, `tick`, `run`, `pause`, `top`, `jobs`, `fg`, `bg` |
| Sync / Deadlock | `banker`, `bregister`, `brequest`, `brelease`, `sem`, `mutex` |
| System | `banner`, `telemetry`, `log`, `journal`, `history`, `alias`, `export`, `env`, `echo`, `clear`, `help`, `man`, `version`, `exit`, `quit`, `logout`, `fmexit`, `watch` |

## Architecture

```text
main.py
mini_os/
  core/
    disk.py          # block disk, bitmap, allocation, compaction, fsck
    filesystem.py    # host-backed tree, metadata, permissions, paths
    scheduler.py     # Round Robin and priority aging
    deadlock.py      # Banker's Algorithm
    journal.py       # append-only journal
    kernel.py        # service wiring and persistence
  cli/
    shell.py         # interactive shell and command runner
    parser.py        # pipes, redirects, chaining
    completion.py    # context-aware completion
    render.py        # tables, banner, telemetry, visualizations
    pager.py         # long output paging
tests/
demo.mosh
```

## Preview

<!--<img src="https://i.postimg.cc/cJkRK4qJ/Screenshot-2026-05-19-183943.png">-->

https://github.com/user-attachments/assets/89cadb8c-02a3-4bc1-98d6-646fdc4e7788



## Testing

```powershell
python -m compileall main.py mini_os
python -m pytest
```

The test suite covers:

- Allocation correctness across all three methods.
- Scheduler fairness and priority aging.
- Banker's Algorithm safe and unsafe requests.
- Permission enforcement.
- Journal replay and persistence.
- Pipe/redirect parsing.
- Completion candidates.
- Host-backed file creation.

## Demo Script

`demo.mosh` exercises the major systems end to end:

```powershell
python main.py demo.mosh
```

It creates folders/files, changes allocation methods, renders disk views, spawns processes, ticks the scheduler, uses Banker's Algorithm, checks the journal, and runs `fsck`.

## Safety Notes

- ABS Manager is intentionally scoped to the current launch directory.
- Created files are normal host files.
- `rm` and `rm -r` delete real files inside that directory.
- Simulated Unix permissions are separate from Windows ACLs.
- `mkfs` resets simulated disk state and metadata; it does not intentionally wipe normal host files.
