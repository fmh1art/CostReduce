#!/usr/bin/env python3
"""Queue one baseline command behind an existing model-backbone driver.

This is deliberately baseline-local.  It prevents a replacement experiment
from exceeding a backbone's concurrency ceiling while an earlier matrix still
owns that entire allowance.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _process_identity(pid: int) -> tuple[int, str] | None:
    proc = Path("/proc") / str(pid)
    try:
        # Linux /proc/<pid>/stat field 22 is the process start time.  The
        # command name can contain spaces/parentheses, so split after the last
        # closing parenthesis before indexing the remaining fields.
        stat = (proc / "stat").read_text(encoding="utf-8")
        tail = stat.rsplit(")", 1)[1].strip().split()
        start_ticks = int(tail[19])
        cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ")
        return start_ticks, cmdline.decode("utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return None


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Queue a baseline command until one exact PID exits."
    )
    parser.add_argument("--wait-pid", type=int, required=True)
    parser.add_argument("--expected-cmd-substring", required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--pid-file", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a command is required after --")
    if args.poll_seconds <= 0 or args.poll_seconds > 60:
        parser.error("--poll-seconds must be in (0, 60]")
    return args


def main() -> int:
    args = parse_args()
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.parent.mkdir(parents=True, exist_ok=True)
    args.pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    initial = _process_identity(args.wait_pid)
    expected_start = initial[0] if initial else None
    matched = bool(
        initial and args.expected_cmd_substring in initial[1]
    )
    payload: dict[str, Any] = {
        "schema_version": "baseline.queued-run.v1",
        "queue_pid": os.getpid(),
        "wait_pid": args.wait_pid,
        "wait_process_start_ticks": expected_start,
        "expected_command_matched_at_enqueue": matched,
        "queued_at": _now(),
        "state": "queued" if matched else "starting",
        "started_at": None,
        "finished_at": None,
        "returncode": None,
        "log": str(args.log.resolve()),
    }
    _atomic_json(args.status, payload)

    while matched:
        current = _process_identity(args.wait_pid)
        if current is None:
            break
        if current[0] != expected_start:
            break
        if args.expected_cmd_substring not in current[1]:
            break
        time.sleep(args.poll_seconds)

    payload["state"] = "running"
    payload["started_at"] = _now()
    _atomic_json(args.status, payload)
    with args.log.open("a", encoding="utf-8") as log:
        log.write(
            f"[queued-baseline] started_at={payload['started_at']} "
            f"queue_pid={os.getpid()}\n"
        )
        log.flush()
        returncode = subprocess.run(
            args.command,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode

    payload["state"] = "complete" if returncode == 0 else "failed"
    payload["finished_at"] = _now()
    payload["returncode"] = returncode
    _atomic_json(args.status, payload)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
