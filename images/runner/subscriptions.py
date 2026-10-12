#!/usr/bin/env python3
"""Resolve the projected active login before launching an agent process."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile

root = Path(os.environ.get("OMNIGENT_SUBSCRIPTIONS_DIR", "/run/omnigent/subscriptions"))
# Resolve one projection generation so selection and credentials cannot mismatch.
root = root.resolve() if not (root / "..data").exists() else (root / "..data").resolve()
selection_file = root / "selection.json"
if not selection_file.exists():
    sys.exit(0)
selection = json.loads(selection_file.read_text())
provider = sys.argv[1]
account = selection.get(provider)
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
