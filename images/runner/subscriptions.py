#!/usr/bin/env python3
"""Resolve the projected active login before launching an agent process.

With --record FILE, also writes the account's ID to FILE, or nothing when the
provider has no account in Settings, so the session can show which account its
agent started with.
"""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile


def record(path, account_id):
    if path is None:
        return
    path = Path(path)
    # Best-effort: the agent starts even when the record can't be written.
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        with os.fdopen(fd, "w") as output:
            output.write(account_id)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except OSError as error:
        print(f"subscriptions: could not record the account in {path}: {error}", file=sys.stderr)


provider = sys.argv[1]
record_file = sys.argv[3] if sys.argv[2:3] == ["--record"] else None
root = Path(os.environ.get("OMNIGENT_SUBSCRIPTIONS_DIR", "/run/omnigent/subscriptions"))
# Resolve one projection generation so selection and credentials cannot mismatch.
root = root.resolve() if not (root / "..data").exists() else (root / "..data").resolve()
selection_file = root / "selection.json"
selection = json.loads(selection_file.read_text()) if selection_file.exists() else {}
account = selection.get(provider)
record(record_file, account["id"] if account else "")
if not account:
    sys.exit(0)
if provider == "claude":
    print((root / "claude-token").read_text(), end="")
else:
    # Refresh tokens remain writable and scoped to the subscription on the PVC.
    shared = Path(os.environ.get("OMNIGENT_CODEX_AUTH_ROOT", "/mnt/codex-home/subscriptions"))
    target = shared / account["id"]
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        revision = target / ".revision"
        if not revision.exists() or revision.read_text() != account["revision"] or not (target / "auth.json").exists():
            fd, temporary = tempfile.mkstemp(dir=target)
            with os.fdopen(fd, "w") as output:
                output.write((root / "codex-auth.json").read_text())
            os.replace(temporary, target / "auth.json")
            revision.write_text(account["revision"])
    print(target / "auth.json", end="")
