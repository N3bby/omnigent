"""Progress bars agents show in the web UI, and the logs streamed under them.

An agent calls the ``sys_session_progress`` tool (the runner's
``0014-agent-progress.patch``) with a label and, when it can tell, a percentage,
and optionally names a log file. The runner posts the bar to
``POST /v1/sessions/{id}/progress`` (``omnigent_progress_api``) and, for a log
file, follows it and posts its new lines to
``POST /v1/sessions/{id}/progress/{bar id}/log``. This module keeps a short
history of each session's bars with their logs, and pushes every change of a
bar to the web UI as a ``session.progress`` event with the session's bars. The
session's snapshot carries them as ``progress``. See
docs/features/agent-progress.md.

A bar belongs to the turn that set it: the web UI shows the newest one above
Working… while the agent works, and every finished one as a line at the end of
its turn, where its log stays readable. A call updates the newest bar, unless
that bar has ended or the user has sent a message since, which starts a new
one. A bar ends when the session stops working (``0022-agent-progress.patch``
reports every status change through :func:`on_status`). Claude's terminal reads
as idle for a second between steps, so a bar that ended less than
:data:`TURN_GAP_S` ago carries on when the agent works again.

Bars and logs live in memory, per server process: a restarted server forgets
them. Nothing here imports Omnigent, so the tests can load it on its own.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

MAX_LABEL_CHARS = 120
"""Longer labels are cut, with an ellipsis."""
MAX_BARS = 20
"""Bars kept per session; the oldest go first."""
MAX_SESSIONS = 1000
"""Sessions whose bars are kept; the least recently updated go first."""
MAX_LOG_FILES = 5
"""Log files one bar can follow."""
MAX_LOG_LINES = 2000
"""Lines kept per bar; older ones are dropped first."""
MAX_LINE_CHARS = 1000
MAX_LOG_CHARS = 32 * 1024 * 1024
"""Log text kept across all sessions; the logs updated longest ago go first."""
TURN_GAP_S = 5.0
"""A bar that ended less than this long ago carries on when the agent works again."""

Publisher = Callable[[str, list[dict[str, Any]]], None]


@dataclass
class _Bar:
    id: str
    label: str
    percent: float | None
    started_at: float
    updated_at: float
    ended_at: float | None = None
    outcome: str | None = None
    """``None`` while the agent works, then ``"done"`` or ``"failed"``."""
    log_files: list[str] = field(default_factory=list)
    log: deque[tuple[int, float, str]] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    log_seq: int = 0
    log_chars: int = 0
    log_dropped: bool = False
    """Whether older lines were dropped, past :data:`MAX_LOG_LINES` or the memory budget."""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "percent": self.percent,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "log_files": list(self.log_files),
            "has_log": self.log_seq > 0,
        }


@dataclass
class _Session:
    bars: list[_Bar] = field(default_factory=list)
    user_message_at: float | None = None


_lock = threading.RLock()
_sessions: OrderedDict[str, _Session] = OrderedDict()
_logs: OrderedDict[tuple[str, str], _Bar] = OrderedDict()
"""Bars with log lines, the least recently appended first, for the memory budget."""
_log_chars = 0
_publisher: Publisher | None = None


def set_publisher(publisher: Publisher | None) -> None:
    """
    Set where changes go: the server's session stream.

    :param publisher: Called with a session id and its bars, oldest first.
    """
    global _publisher
    _publisher = publisher


def parse(body: Any) -> dict[str, Any] | None:
    """
    Check an update and normalise it.

    :param body: ``{"label": "Running tests", "percent": 40,
        "log_file": "/home/omnigent/tests.log"}``; ``percent`` may be left out
        or ``null`` when the agent can't tell how far along it is, and
        ``log_file`` names a file the runner follows. ``{"clear": true}`` ends
        the bar now.
    :returns: ``{"label", "percent", "log_file"}``, or ``None`` to end the bar.
    :raises ValueError: When the update is malformed, in words for the agent.
    """
    if not isinstance(body, dict):
        raise ValueError("the update must be an object")
    clear = body.get("clear", False)
    if not isinstance(clear, bool):
        raise ValueError("clear must be true or false")
    if clear:
        return None
    label = body.get("label")
    if not isinstance(label, str) or not " ".join(label.split()):
        raise ValueError("label must say what's in progress, e.g. 'Running the test suite'")
    label = " ".join(label.split())
    if len(label) > MAX_LABEL_CHARS:
        label = label[: MAX_LABEL_CHARS - 1].rstrip() + "…"
    percent = body.get("percent")
    if percent is not None:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise ValueError("percent must be a number from 0 to 100, or null")
        if not math.isfinite(percent) or percent < 0 or percent > 100:
            raise ValueError("percent must be a number from 0 to 100, or null")
        percent = float(percent)
    log_file = body.get("log_file")
    if log_file is not None:
        if not isinstance(log_file, str) or not log_file.startswith("/") or "\n" in log_file:
            raise ValueError("log_file must be an absolute path, e.g. '/home/omnigent/tests.log'")
    return {"label": label, "percent": percent, "log_file": log_file}


def update(session_id: str, body: Any, *, now: float | None = None) -> dict[str, Any] | None:
    """
    Set the session's bar, or end it, and push the change.

    :param session_id: The session, e.g. ``"conv_abc123"``.
    :param body: The update, as :func:`parse` takes it.
    :param now: The time, for tests.
    :returns: The bar as it now is, with its ``id``, or ``None`` once ended.
    :raises ValueError: When the update is malformed.
    """
    change = parse(body)
    at = time.time() if now is None else now
    with _lock:
        session = _session(session_id)
        current = session.bars[-1] if session.bars else None
        if change is None:
            if current is None or current.outcome is not None:
                return None
            current.ended_at = at
            current.outcome = "done"
            result = None
        else:
            if current is None or not _carries_on(session, current, at):
                current = _Bar(
                    id=f"bar_{uuid.uuid4().hex[:12]}",
                    label=change["label"],
                    percent=change["percent"],
                    started_at=at,
                    updated_at=at,
                )
                session.bars.append(current)
                for old in session.bars[:-MAX_BARS]:
                    _forget_log(session_id, old)
                del session.bars[:-MAX_BARS]
            current.label = change["label"]
            current.percent = change["percent"]
            current.updated_at = at
            current.ended_at = None
            current.outcome = None
            log_file = change["log_file"]
            if log_file and log_file not in current.log_files:
                current.log_files = [*current.log_files, log_file][-MAX_LOG_FILES:]
            result = current.public()
        bars = _public(session)
    _publish(session_id, bars)
    return result


def _carries_on(session: _Session, bar: _Bar, at: float) -> bool:
    """Whether an update at *at* goes to *bar* rather than starting a new one."""
    if session.user_message_at is not None and session.user_message_at > bar.updated_at:
        return False
    return bar.ended_at is None or at - bar.ended_at < TURN_GAP_S


def on_status(session_id: str, status: str, *, now: float | None = None) -> None:
    """
    Follow a session's status: end its bar when it stops working.

    :param session_id: The session.
    :param status: ``"running"``, ``"waiting"``, ``"idle"`` or ``"failed"``.
    :param now: The time, for tests.
    """
    at = time.time() if now is None else now
    with _lock:
        session = _sessions.get(session_id)
        if session is None or not session.bars:
            return
        bar = session.bars[-1]
        if status in ("idle", "failed"):
            if bar.outcome is None:
                bar.ended_at = at
                bar.outcome = "failed" if status == "failed" else "done"
            elif status == "failed" and bar.outcome == "done":
                # The turn failed just after the agent went quiet.
                bar.outcome = "failed"
            else:
                return
        elif status == "running":
            # A pause between steps, not the end of the turn.
            if bar.ended_at is None or at - bar.ended_at >= TURN_GAP_S:
                return
            if session.user_message_at is not None and session.user_message_at > bar.updated_at:
                return
            bar.ended_at = None
            bar.outcome = None
        else:
            return
        bars = _public(session)
    _publish(session_id, bars)


def on_user_message(session_id: str, *, now: float | None = None) -> None:
    """
    Note a message from the user: the next update starts a new bar.

    :param session_id: The session.
    :param now: The time, for tests.
    """
    with _lock:
        session = _sessions.get(session_id)
        if session is not None:
            session.user_message_at = time.time() if now is None else now


def append_log(
    session_id: str, bar_id: str, lines: Iterable[Any], *, now: float | None = None
) -> bool:
    """
    Add lines a bar's log file gained.

    :param session_id: The session.
    :param bar_id: The bar the file belongs to, e.g. ``"bar_1a2b3c4d5e6f"``.
    :param lines: The new lines, without line endings.
    :param now: The time, for tests.
    :returns: Whether the bar is still kept; the runner stops following its
        file when it isn't.
    """
    global _log_chars
    at = time.time() if now is None else now
    with _lock:
        session = _sessions.get(session_id)
        bar = next((b for b in session.bars if b.id == bar_id), None) if session else None
        if bar is None:
            return False
        first = bar.log_seq == 0
        for line in lines:
            text = line if isinstance(line, str) else str(line)
            text = text[:MAX_LINE_CHARS]
            if len(bar.log) == bar.log.maxlen:
                bar.log_chars -= len(bar.log[0][2])
                _log_chars -= len(bar.log[0][2])
                bar.log_dropped = True
            bar.log_seq += 1
            bar.log.append((bar.log_seq, at, text))
            bar.log_chars += len(text)
            _log_chars += len(text)
        key = (session_id, bar_id)
        _logs.pop(key, None)
        _logs[key] = bar
        while _log_chars > MAX_LOG_CHARS and _logs:
            _, oldest = _logs.popitem(last=False)
            _drop_lines(oldest)
        bars = _public(session) if first and bar.log_seq else None
    if bars is not None:
        _publish(session_id, bars)
    return True


def log_page(session_id: str, bar_id: str, after: int = 0) -> dict[str, Any] | None:
    """
    A bar's log lines after line *after*.

    :param session_id: The session.
    :param bar_id: The bar.
    :param after: The last line the caller has, ``0`` for all.
    :returns: ``{"lines": [{"seq", "t", "text"}], "next", "dropped",
        "log_files"}``, or ``None`` for an unknown bar.
    """
    with _lock:
        session = _sessions.get(session_id)
        bar = next((b for b in session.bars if b.id == bar_id), None) if session else None
        if bar is None:
            return None
        lines = [{"seq": s, "t": t, "text": text} for s, t, text in bar.log if s > after]
        return {
            "lines": lines,
            "next": bar.log_seq,
            "dropped": bar.log_dropped,
            "log_files": list(bar.log_files),
        }


def snapshot(session_id: str) -> list[dict[str, Any]] | None:
    """
    A session's bars, oldest first, for its snapshot.

    :param session_id: The session.
    :returns: Each bar as :meth:`_Bar.public` has it, or ``None`` without any.
    """
    with _lock:
        session = _sessions.get(session_id)
        return _public(session) if session and session.bars else None


def _session(session_id: str) -> _Session:
    session = _sessions.pop(session_id, None) or _Session()
    _sessions[session_id] = session
    while len(_sessions) > MAX_SESSIONS:
        old_id, old = _sessions.popitem(last=False)
        for bar in old.bars:
            _forget_log(old_id, bar)
    return session


def _public(session: _Session) -> list[dict[str, Any]]:
    return [bar.public() for bar in session.bars]


def _drop_lines(bar: _Bar) -> None:
    global _log_chars
    if bar.log:
        bar.log_dropped = True
    _log_chars -= bar.log_chars
    bar.log_chars = 0
    bar.log.clear()


def _forget_log(session_id: str, bar: _Bar) -> None:
    _logs.pop((session_id, bar.id), None)
    _drop_lines(bar)


def _publish(session_id: str, bars: list[dict[str, Any]]) -> None:
    publisher = _publisher
    if publisher is not None:
        publisher(session_id, bars)
