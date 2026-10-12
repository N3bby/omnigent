#!/usr/bin/env python3
"""Bridge Claude's official setup-token flow without exposing its terminal output."""
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import time

import pyte
from urllib.parse import urlsplit

ROOT = Path(os.environ.get("OMNIGENT_LOGIN_DIR", "/tmp/claude-login"))
TOKEN = re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_-]+(?=[^A-Za-z0-9_-])")


def authorization_url(output):
    # OSC hyperlinks contain the full URL even when the visible text wraps.
    urls = re.findall(r"\x1b\]8;[^;]*;(https://[^\x1b\x07\s]+)[\x1b\x07]", output)
    for url in urls:
        parts = urlsplit(url)
        if parts.hostname in ("claude.com", "claude.ai") and parts.path == "/cai/oauth/authorize":
            return url
    return None


class LoginTerminal:
    """Reconstruct Claude's screen; stripping ANSI loses cursor-addressed text."""
    def __init__(self):
        self.screen = pyte.HistoryScreen(4096, 80, history=200)
        self.stream = pyte.ByteStream(self.screen)

    def feed(self, chunk):
        self.stream.feed(chunk)

    def text(self):
        return "\n".join(self.screen.display)

    def token(self):
        text = self.text()
        # Wait for the following line, so a split PTY read cannot save a prefix.
        if "Store this token securely" not in text:
            return None
        match = TOKEN.search(text)
        return match.group() if match else None


def status(value):
    path = ROOT / "status.json"
    temporary = ROOT / ".status.json"
    temporary.write_text(json.dumps(value))
    os.replace(temporary, path)


def main():
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    status({"status": "starting"})
    master, slave = pty.openpty()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "ANTHROPIC", "OMNIGENT"))}
    env.update(HOME=str(ROOT), BROWSER="true", TERM="xterm-256color", DISABLE_AUTOUPDATER="1")
    import fcntl
    import struct
    import termios
    # Set dimensions before the child reads them; a wide screen avoids wrapping tokens.
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 80, 4096, 0, 0))
    proc = subprocess.Popen([os.environ.get("OMNIGENT_CLAUDE_LOGIN_BINARY", "/usr/local/bin/claude-real"), "setup-token"],
        stdin=slave, stdout=slave, stderr=slave, cwd=ROOT, env=env, start_new_session=True)
    os.close(slave)
    output = ""
    terminal = LoginTerminal()
    deadline = time.monotonic() + 850
    try:
        while time.monotonic() < deadline:
            code_file = ROOT / "code.txt"
            if code_file.exists():
                code = code_file.read_text().strip()
                code_file.unlink()
                if re.fullmatch(r"[A-Za-z0-9_-]+(?:#[A-Za-z0-9_-]+)?", code) and len(code) <= 2048:
                    os.write(master, b"\x1b[200~" + code.encode() + b"\x1b[201~\r")
                    status({"status": "exchanging"})
            if select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 65536)
                    if not chunk:
                        break
                    terminal.feed(chunk)
                    output += chunk.decode(errors="replace")
                except OSError:
                    break
                url = authorization_url(output)
                if url and not (ROOT / "credential.txt").exists():
                    current = json.loads((ROOT / "status.json").read_text())
                    if current["status"] == "starting":
                        status({"status": "waiting", "url": url})
                token = terminal.token()
                if token:
                    (ROOT / "credential.txt").write_text(token)
                    status({"status": "complete"})
                    return
                if any(error in terminal.text() for error in (
                    "OAuth error:", "Invalid authorization code", "Authentication failed"
                )):
                    status({"status": "failed"})
                    return
                output = output[-256000:]
            # Keep draining the PTY after child exit: its final token may still be buffered.
        token = terminal.token()
        if token:
            (ROOT / "credential.txt").write_text(token)
            status({"status": "complete"})
        else:
            status({"status": "expired"})
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        os.close(master)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never write CLI output or submitted codes to the Pod log.
        status({"status": "failed"})
        sys.exit(1)
