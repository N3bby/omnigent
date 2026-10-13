#!/opt/venv/bin/python3
"""Tell the Omnigent server how this runner Pod's start is going.

The server shows each step of a sandbox's start in the session (see
docs/features/startup-progress.md). It sees the Pod from Kubernetes, but not
what runs inside it once it's up: the repositories' setup scripts, Tailscale
and Docker. Their services call this to report them:

    omnigent-startup-report step setup roadpass running "Running setup.sh"
    omnigent-startup-report step setup roadpass failed "Exit status 127" --log FILE
    omnigent-startup-report step host Tailscale done omnigent-e1dcab9b.tail1234.ts.net
    omnigent-startup-report follow setup FILE PID

``follow`` sends the lines a file gets until process PID exits. Reports go to
$OMNIGENT_STARTUP_REPORT_URL with the host's launch token; without them (an
older server, or not a sandbox) it does nothing. It never fails its caller:
a report that doesn't arrive only leaves a step out of date.

Lines are sent with the values of the repositories' variables hidden, so a
setup script that prints a token doesn't put it on the page.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request

URL = os.environ.get("OMNIGENT_STARTUP_REPORT_URL", "")
TOKEN = os.environ.get("OMNIGENT_HOST_TOKEN", "")
ENV_DIR = os.environ.get("OMNIGENT_REPO_ENV_DIR", "/run/omnigent/repo-env")
POLL_S = float(os.environ.get("OMNIGENT_STARTUP_REPORT_POLL_S", "1.5"))
BATCH = 200
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def secrets() -> list[str]:
    """The values of the repositories' variables, longest first."""
    values: set[str] = set()
    try:
        secret_dirs = os.listdir(ENV_DIR)
    except OSError:
        return []
    for secret in secret_dirs:
        directory = os.path.join(ENV_DIR, secret)
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not NAME.match(name):
                continue
            try:
                with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as f:
                    value = f.read().strip()
            except OSError:
                continue
            if len(value) >= 6:
                values.add(value)
    return sorted(values, key=len, reverse=True)


def hide(text: str, values: list[str]) -> str:
    for value in values:
        text = text.replace(value, "[hidden]")
    return text


def post(body: dict[str, object]) -> None:
    if not URL or not TOKEN:
        return
    request = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Omnigent-Host-Token": TOKEN},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except Exception:  # noqa: BLE001 - a lost report only leaves a step out of date
        pass


def tail(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def step(args: list[str]) -> int:
    log_file = None
    if "--log" in args:
        i = args.index("--log")
        log_file = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2 :]
    if len(args) < 3:
        print("usage: omnigent-startup-report step STEP CHILD STATE [DETAIL] [--log FILE]", file=sys.stderr)
        return 2
    entry: dict[str, object] = {"step": args[0], "child": args[1], "state": args[2]}
    if len(args) > 3:
        entry["detail"] = args[3]
    if log_file:
        entry["log_tail"] = hide(tail(log_file), secrets())
    post({"steps": [entry]})
    return 0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A process that exited but isn't reaped yet still answers signal 0.
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as f:
            return f.read().rpartition(")")[2].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def follow(args: list[str]) -> int:
    if len(args) != 3:
        print("usage: omnigent-startup-report follow SOURCE FILE PID", file=sys.stderr)
        return 2
    source, path, pid_text = args
    pid = int(pid_text)
    values = secrets()
    offset = 0
    pending = b""
    while True:
        running = alive(pid)
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                chunk = f.read()
        except OSError:
            chunk = b""
        offset += len(chunk)
        data = pending + chunk
        lines = data.split(b"\n")
        pending = lines.pop()
        if not running and pending:
            lines.append(pending)
            pending = b""
        now = time.time()
        entries = [
            {"source": source, "text": hide(line.decode("utf-8", errors="replace"), values), "at": now}
            for line in lines
            if line.strip()
        ]
        for start in range(0, len(entries), BATCH):
            post({"log": entries[start : start + BATCH]})
        if not running:
            return 0
        time.sleep(POLL_S)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    command, rest = argv[0], argv[1:]
    try:
        if command == "step":
            return step(rest)
        if command == "follow":
            return follow(rest)
    except Exception:  # noqa: BLE001 - never fail the caller
        return 0
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
