"""How a sandbox session starts, step by step, with its log.

Before a session's agent answers, its sandbox goes through several steps: the
server creates the Sandbox, Kubernetes schedules its Pod and gets the runner
image, the init container clones the repositories, the host starts and connects
back, the repositories' setup scripts run, and the agent starts. Upstream only
reports four coarse stages (``session.sandbox_status``). This module keeps a
record of each session's current or last start: its steps and their times, why
it failed in plain words, and its log. See docs/features/startup-progress.md.

Who reports what:

- Every stage upstream publishes goes through :func:`on_stage`
  (``images/server/patches/0016-startup-progress.patch``).
- The Kubernetes launcher's Pod poll reports scheduling, the image, the clone
  and the host container (:func:`observe_pod`, :func:`observe_events`,
  :func:`observe_container_log`). It finds the session through a context
  variable the launch and wake tasks set with :func:`bind`.
- The runner Pod reports its setup scripts, Tailscale and Docker to
  ``POST /v1/hosts/{host_id}/startup`` (:func:`host_report`).

Records live in memory, per server process, until the session's next start.
Changes are pushed as ``session.startup`` events through the publisher the
server sets with :func:`set_publisher`. Nothing here imports Omnigent, so the
tests can load it on its own.
"""

from __future__ import annotations

import asyncio
import contextvars
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

STEP_ORDER = ("sandbox", "schedule", "image", "clone", "host", "setup", "agent")
"""Every step a start can have, in the order they're shown."""

STATES = frozenset({"pending", "running", "done", "failed", "skipped"})
_FINAL_STATES = frozenset({"done", "failed", "skipped"})

LOG_SOURCES = frozenset({"server", "k8s", "clone", "host", "setup", "agent"})
"""Where a log line came from; the web UI filters on these."""

MAX_LOG_LINES = 3000
"""Lines kept per start; older ones are dropped first."""
MAX_LINE_CHARS = 2000
MAX_DETAIL_CHARS = 300
MAX_RECORDS = 500
"""Sessions whose last start is kept; the oldest finished ones go first."""

PREP_MARKER = "omnigent-prep:"
"""Prefix of the lines the init container prints for each repository."""

REPORT_URL_ENV_VAR = "OMNIGENT_STARTUP_REPORT_URL"
"""Where the runner Pod posts its reports: ``…/v1/hosts/<host id>/startup``."""

# Steps the runner Pod may report, and the children it may report under them.
_HOST_CHILDREN = frozenset({"Tailscale", "Docker"})
_POD_LOG_SOURCES = frozenset({"setup", "host"})

Publisher = Callable[[str, dict[str, Any]], None]

_lock = threading.RLock()
_records: dict[str, _Record] = {}
_host_sessions: dict[str, str] = {}
_wakes: set[str] = set()
_publisher: Publisher | None = None
_session: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "omnigent_startup_session", default=None
)

# Runners that exited before connecting: their error, and anyone waiting.
_runner_exits: dict[str, str] = {}
_runner_waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Future[str]]]] = {}


@dataclass
class _Child:
    """One repository's clone or setup script, or one of the host's services."""

    name: str
    state: str = "pending"
    detail: str | None = None
    started_at: float | None = None
    ended_at: float | None = None


@dataclass
class _Step:
    """One step of a start."""

    id: str
    state: str = "pending"
    detail: str | None = None
    tone: str | None = None
    """``"warning"`` while a running step is held up, e.g. no room on the VM."""
    started_at: float | None = None
    ended_at: float | None = None
    children: dict[str, _Child] = field(default_factory=dict)


@dataclass
class _Record:
    """A session's current or last start."""

    session_id: str
    kind: str
    """``"launch"`` for a new sandbox, ``"wake"`` for a stopped one."""
    started_at: float
    steps: dict[str, _Step]
    outcome: str = "running"
    """``"running"``, ``"ready"``, ``"failed"`` or ``"cancelled"``."""
    ended_at: float | None = None
    failure: dict[str, str] | None = None
    warning: dict[str, str] | None = None
    host_id: str | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    seq: int = 0
    seen_events: set[str] = field(default_factory=set)
    log_offsets: dict[str, int] = field(default_factory=dict)
    setup_tails: dict[str, str] = field(default_factory=dict)


# ── wiring ──────────────────────────────────────────────


def set_publisher(publisher: Publisher | None) -> None:
    """
    Set where record changes go, normally the session's event stream.

    :param publisher: Called with the session id and the ``session.startup``
        event payload, from any thread.
    """
    global _publisher
    _publisher = publisher


def bind(session_id: str) -> None:
    """
    Mark the current task as starting *session_id*'s sandbox.

    The launch and wake tasks call this, so code they run, on worker threads
    too (``asyncio.to_thread`` copies the context), can find the session with
    :func:`current_session`.

    :param session_id: Session/conversation identifier.
    """
    _session.set(session_id)


def current_session() -> str | None:
    """The session the current task is starting, or ``None``."""
    return _session.get()


def expect_wake(session_id: str) -> None:
    """
    Make *session_id*'s next start a wake: a stopped sandbox starting again.

    :param session_id: Session/conversation identifier.
    """
    with _lock:
        _wakes.add(session_id)


def attach_host(host_id: str, session_id: str | None = None) -> None:
    """
    Remember which session *host_id*'s Pod belongs to, for its reports.

    :param host_id: The managed host the sandbox runs.
    :param session_id: The session, or ``None`` for the current task's.
    """
    session_id = session_id or current_session()
    if not session_id:
        return
    with _lock:
        _host_sessions[host_id] = session_id
        record = _records.get(session_id)
        if record is not None:
            record.host_id = host_id


# ── records ─────────────────────────────────────────────


def _now() -> float:
    return time.time()


def _clip(text: str | None, limit: int = MAX_DETAIL_CHARS) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _begin(session_id: str, kind: str) -> _Record:
    now = _now()
    steps = {step_id: _Step(step_id) for step_id in ("sandbox", "host", "agent")}
    steps["sandbox"].state = "running"
    steps["sandbox"].started_at = now
    previous = _records.get(session_id)
    record = _Record(session_id=session_id, kind=kind, started_at=now, steps=steps)
    if previous is not None:
        record.host_id = previous.host_id
    _records[session_id] = record
    _evict()
    _append_log(
        record,
        "server",
        "Waking the stopped sandbox" if kind == "wake" else "Creating the sandbox",
        "info",
        now,
    )
    return record


def _evict() -> None:
    if len(_records) <= MAX_RECORDS:
        return
    finished = sorted(
        (r for r in _records.values() if r.outcome != "running"),
        key=lambda r: r.ended_at or r.started_at,
    )
    for record in finished[: len(_records) - MAX_RECORDS]:
        _records.pop(record.session_id, None)


def begin(session_id: str, kind: str = "launch") -> None:
    """
    Start a new record for *session_id*, replacing its last one.

    :param session_id: Session/conversation identifier.
    :param kind: ``"launch"`` or ``"wake"``.
    """
    with _lock:
        _wakes.discard(session_id)
        _begin(session_id, kind)
    _publish(session_id)


def _step(record: _Record, step_id: str) -> _Step:
    step = record.steps.get(step_id)
    if step is None:
        step = _Step(step_id)
        record.steps[step_id] = step
        record.steps = {
            key: record.steps[key] for key in STEP_ORDER if key in record.steps
        }
    return step


def _move(item: _Step | _Child, state: str, detail: str | None, now: float) -> bool:
    """Move a step or child to *state*. A finished one doesn't go back to running."""
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    if item.state in _FINAL_STATES and state in ("pending", "running"):
        return False
    changed = item.state != state
    if state == "running" and item.started_at is None:
        item.started_at = now
    if state in _FINAL_STATES and changed:
        if item.started_at is None and state != "skipped":
            item.started_at = now
        item.ended_at = now
    item.state = state
    if detail is None and changed and state in ("done", "skipped"):
        # "Connecting to Omnigent" says what a running step does, not how it ended.
        item.detail = None
    if detail is not None:
        detail = _clip(detail)
        changed = changed or item.detail != detail
        item.detail = detail
    return changed


def _roll_up(step: _Step, now: float) -> None:
    """Derive a step with children from them: failed, running, done or skipped."""
    states = [child.state for child in step.children.values()]
    if not states:
        return
    if "failed" in states:
        state = "failed"
    elif "running" in states or "pending" in states:
        state = "running"
    elif "done" in states:
        state = "done"
    else:
        state = "skipped"
    if state in ("running", "pending") and step.state in _FINAL_STATES:
        # A late child (a second setup script) reopens the step.
        step.state = "running"
        step.ended_at = None
    _move(step, state, None, now)


def _set(
    record: _Record,
    step_id: str,
    state: str,
    detail: str | None = None,
    *,
    child: str | None = None,
    tone: str | None = None,
    now: float | None = None,
) -> bool:
    now = _now() if now is None else now
    step = _step(record, step_id)
    if child is None:
        changed = _move(step, state, detail, now)
        if step.tone != tone and (state == "running" or tone is None):
            step.tone = tone
            changed = True
        return changed
    item = step.children.get(child)
    if item is None:
        item = _Child(_clip(child, 100) or "?")
        step.children[child] = item
    changed = _move(item, state, detail, now)
    _roll_up(step, now)
    return changed


def step(
    session_id: str | None,
    step_id: str,
    state: str,
    detail: str | None = None,
    *,
    child: str | None = None,
    tone: str | None = None,
) -> None:
    """
    Move one step, or one of its children, of the session's current start.

    A no-op when the session has no record, so callers needn't check.

    :param session_id: Session/conversation identifier, or ``None``.
    :param step_id: One of :data:`STEP_ORDER`.
    :param state: One of :data:`STATES`.
    :param detail: One line shown under the step, e.g. ``"on vm-omnigent"``.
    :param child: A repository or service under the step, e.g. ``"roadpass"``.
    :param tone: ``"warning"`` while the step is held up.
    """
    if not session_id or step_id not in STEP_ORDER:
        return
    with _lock:
        record = _records.get(session_id)
        if record is None:
            return
        changed = _set(record, step_id, state, detail, child=child, tone=tone)
    if changed:
        _publish(session_id)


def _append_log(record: _Record, source: str, text: str, level: str, at: float) -> None:
    for line in str(text).splitlines() or [""]:
        line = line.rstrip()
        if not line:
            continue
        record.seq += 1
        record.log.append(
            {
                "seq": record.seq,
                "at": at,
                "source": source if source in LOG_SOURCES else "server",
                "level": level if level in ("info", "warning", "error") else "info",
                "text": line[:MAX_LINE_CHARS],
            }
        )
    if len(record.log) > MAX_LOG_LINES:
        del record.log[: len(record.log) - MAX_LOG_LINES]


def log(
    session_id: str | None,
    source: str,
    text: str,
    level: str = "info",
    at: float | None = None,
) -> None:
    """
    Add lines to the session's startup log. Log lines aren't pushed; the web
    UI fetches them while its log panel is open.

    :param session_id: Session/conversation identifier, or ``None``.
    :param source: One of :data:`LOG_SOURCES`.
    :param text: One or more lines.
    :param level: ``"info"``, ``"warning"`` or ``"error"``.
    :param at: When it happened, in Unix seconds; now by default.
    """
    if not session_id:
        return
    with _lock:
        record = _records.get(session_id)
        if record is not None:
            _append_log(record, source, text, level, _now() if at is None else at)


def cancelled(session_id: str) -> None:
    """
    Settle the session's start as cancelled ("Cancel start" in the composer).

    :param session_id: Session/conversation identifier.
    """
    with _lock:
        record = _records.get(session_id)
        if record is None or record.outcome != "running":
            return
        now = _now()
        record.outcome = "cancelled"
        record.ended_at = now
        _append_log(record, "server", "The start was cancelled", "info", now)
    _publish(session_id)


def on_stage(session_id: str, stage: str, error: str | None = None) -> None:
    """
    Follow upstream's launch stages, which every start goes through.

    :param session_id: Session/conversation identifier.
    :param stage: ``provisioning``, ``cloning``, ``starting``, ``connecting``,
        ``ready`` or ``failed``.
    :param error: Why it failed, for ``failed``.
    """
    with _lock:
        record = _records.get(session_id)
        now = _now()
        if stage == "provisioning":
            if record is None or record.outcome != "running":
                kind = "wake" if session_id in _wakes else "launch"
                _wakes.discard(session_id)
                record = _begin(session_id, kind)
        elif record is None:
            return
        elif stage == "starting":
            _set(record, "sandbox", "running", now=now)
        elif stage == "cloning":
            _set(record, "clone", "running", now=now)
        elif stage == "connecting":
            if record.outcome != "running":
                return
            for step_id in ("sandbox", "schedule", "image", "clone", "host"):
                existing = record.steps.get(step_id)
                if existing is not None and existing.state in ("pending", "running"):
                    _set(record, step_id, "done", now=now)
            _set(record, "agent", "running", "Launching the agent", now=now)
            _append_log(record, "server", "The host connected; launching the agent", "info", now)
        elif stage == "ready":
            if record.outcome != "running":
                return
            for step_id, existing in record.steps.items():
                if step_id != "setup" and existing.state in ("pending", "running"):
                    _set(record, step_id, "done", now=now)
            record.outcome = "ready"
            record.ended_at = now
            _append_log(
                record,
                "server",
                f"The agent is ready, {now - record.started_at:.0f}s after the start",
                "info",
                now,
            )
        elif stage == "failed":
            if record.outcome not in ("running", "failed"):
                return
            failure = explain(error or "")
            failed_step = _failed_step(record, failure.get("step"))
            if failure.get("child") and failed_step == "clone":
                _set(record, "clone", "failed", child=failure["child"], now=now)
            _set(record, failed_step, "failed", now=now)
            record.outcome = "failed"
            record.ended_at = record.ended_at or now
            record.failure = {
                key: value for key, value in failure.items() if key not in ("step", "child")
            }
            record.failure["raw"] = error or ""
            _append_log(record, "server", f"The start failed: {error or 'no reason given'}", "error", now)
        else:
            return
    _publish(session_id)


def _failed_step(record: _Record, hint: str | None) -> str:
    if hint in record.steps or hint in ("schedule", "image", "clone"):
        return hint  # type: ignore[return-value]
    for step_id, existing in record.steps.items():
        if step_id != "setup" and existing.state == "running":
            return step_id
    return "agent"


# ── reading ─────────────────────────────────────────────


def snapshot(session_id: str) -> dict[str, Any] | None:
    """
    The session's current or last start, as the web UI reads it.

    :param session_id: Session/conversation identifier.
    :returns: ``None`` when the session hasn't started since the server did.
    """
    with _lock:
        record = _records.get(session_id)
        return None if record is None else _snapshot(record)


def _snapshot(record: _Record) -> dict[str, Any]:
    return {
        "kind": record.kind,
        "outcome": record.outcome,
        "started_at": record.started_at,
        "ended_at": record.ended_at,
        "steps": [
            {
                "id": s.id,
                "state": s.state,
                "detail": s.detail,
                "tone": s.tone,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "children": [
                    {
                        "name": c.name,
                        "state": c.state,
                        "detail": c.detail,
                        "started_at": c.started_at,
                        "ended_at": c.ended_at,
                    }
                    for c in s.children.values()
                ],
            }
            for s in record.steps.values()
        ],
        "failure": dict(record.failure) if record.failure else None,
        "warning": dict(record.warning) if record.warning else None,
        "log_size": record.seq,
    }


def log_page(session_id: str, after: int = 0, limit: int = 500) -> dict[str, Any] | None:
    """
    Startup log lines after sequence number *after*.

    :param session_id: Session/conversation identifier.
    :param after: The last ``seq`` the caller has; ``0`` for all.
    :param limit: Most lines to return.
    :returns: ``{"started_at", "lines", "next", "live"}``, or ``None`` with no record.
        ``live`` is false once nothing more is expected.
    """
    with _lock:
        record = _records.get(session_id)
        if record is None:
            return None
        lines = [line for line in record.log if line["seq"] > after][: max(1, limit)]
        setup = record.steps.get("setup")
        live = record.outcome == "running" or (
            setup is not None and setup.state in ("pending", "running")
        )
        return {
            "started_at": record.started_at,
            "lines": [dict(line) for line in lines],
            "next": lines[-1]["seq"] if lines else after,
            "live": live,
        }


def _publish(session_id: str) -> None:
    publisher = _publisher
    if publisher is None:
        return
    payload = snapshot(session_id)
    if payload is None:
        return
    try:
        publisher(session_id, payload)
    except Exception:  # noqa: BLE001 - progress must never break a start
        pass


# ── the runner's own reports ─────────────────────────────


def host_report(host_id: str, body: dict[str, Any]) -> bool:
    """
    Take a report from a runner Pod: its setup scripts, Tailscale and Docker.

    The route authenticates the Pod by its host's launch token first.

    :param host_id: The managed host the Pod runs.
    :param body: ``{"steps": [{"step", "child", "state", "detail", "log_tail"}],
        "log": [{"source", "text", "level", "at"}]}``. Unknown steps, children,
        states and sources are ignored.
    :returns: ``False`` when no start is known for the host.
    """
    with _lock:
        session_id = _host_sessions.get(host_id)
        record = _records.get(session_id) if session_id else None
        if record is None or session_id is None:
            return False
        now = _now()
        changed = False
        for entry in _items(body.get("steps"), 50):
            step_id = entry.get("step")
            child = entry.get("child")
            state = entry.get("state")
            if state not in STATES or not isinstance(child, str) or not child.strip():
                continue
            child = child.strip()[:100]
            if step_id == "host" and child not in _HOST_CHILDREN:
                continue
            if step_id not in ("setup", "host"):
                continue
            detail = entry.get("detail") if isinstance(entry.get("detail"), str) else None
            changed |= _set(record, step_id, state, detail, child=child, now=now)
            tail = entry.get("log_tail")
            if step_id == "setup" and isinstance(tail, str):
                record.setup_tails[child] = tail[-4000:]
            if step_id == "setup" and state == "failed":
                record.warning = {
                    "title": f"{child}'s setup script failed",
                    "body": (
                        f"{detail}. " if detail else ""
                    )
                    + "The agent started anyway. Its log is in the startup log, "
                    f"and in ~/.local/state/omnigent-repo-setup/{child}.log in the sandbox.",
                    "log": record.setup_tails.get(child, ""),
                    "source": f"~/.local/state/omnigent-repo-setup/{child}.log",
                    "child": child,
                }
                changed = True
        for entry in _items(body.get("log"), 500):
            source = entry.get("source")
            text = entry.get("text")
            if source not in _POD_LOG_SOURCES or not isinstance(text, str):
                continue
            at = entry.get("at")
            at = float(at) if isinstance(at, (int, float)) and abs(at - now) < 3600 else now
            level = entry.get("level") if entry.get("level") in ("warning", "error") else "info"
            _append_log(record, source, text, level, at)
    if changed:
        _publish(session_id)
    return True


def _items(value: object, limit: int) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


# ── how long a start may take ───────────────────────────

_pod_budget_s = 0.0


def note_pod_budget(seconds: float) -> None:
    """
    Note how long the launcher waits for a Pod to start.

    A message sent while the sandbox starts waits for the start, for the
    host's online timeout plus this, so it isn't turned away while
    Kubernetes is still allowed to be starting the Pod.

    :param seconds: The launcher's Pod-start budget.
    """
    global _pod_budget_s
    with _lock:
        _pod_budget_s = max(_pod_budget_s, float(seconds))


def pod_budget_s() -> float:
    """The longest Pod-start budget a launcher has used, or 0 before any start."""
    return _pod_budget_s


# ── runners that exit while starting ─────────────────────


def runner_exited(runner_id: str, error: str) -> None:
    """
    Note that a runner exited before connecting, waking anyone waiting on it.

    :param runner_id: The runner's id.
    :param error: The host's report: exit code and log tail.
    """
    with _lock:
        _runner_exits[runner_id] = error
        while len(_runner_exits) > 200:
            _runner_exits.pop(next(iter(_runner_exits)))
        waiters = _runner_waiters.pop(runner_id, [])
    for loop, future in waiters:
        loop.call_soon_threadsafe(_resolve, future, error)


def _resolve(future: asyncio.Future[str], error: str) -> None:
    if not future.done():
        future.set_result(error)


async def runner_exit(runner_id: str) -> str:
    """
    Wait until *runner_id* is reported to have exited.

    :param runner_id: The runner's id.
    :returns: The host's report.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()
    with _lock:
        error = _runner_exits.pop(runner_id, None)
        if error is None:
            _runner_waiters.setdefault(runner_id, []).append((loop, future))
    if error is not None:
        return error
    try:
        return await future
    finally:
        with _lock:
            waiters = _runner_waiters.get(runner_id, [])
            if (loop, future) in waiters:
                waiters.remove((loop, future))
            if not waiters:
                _runner_waiters.pop(runner_id, None)


# ── Kubernetes ──────────────────────────────────────────


def _get(obj: object, *path: str) -> Any:
    for name in path:
        if obj is None:
            return None
        obj = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return obj


def _container(statuses: object, name: str) -> object | None:
    for status in statuses or []:  # type: ignore[union-attr]
        if _get(status, "name") == name:
            return status
    return None


def _started(status: object | None) -> bool:
    if status is None:
        return False
    return bool(
        _get(status, "state", "running")
        or _get(status, "state", "terminated")
        or _get(status, "last_state", "terminated")
    )


# Runner Pods ask for a TUN device (kubernetes/platform/tun-device-plugin.yaml),
# which only that plugin offers.
_NO_TUN = re.compile(r"insufficient omnigent\.dev/tun", re.I)


def plain_unschedulable(message: str | None) -> str:
    """
    Say why Kubernetes can't place the Pod, from its scheduler message.

    :param message: e.g. ``"0/1 nodes are available: 1 Insufficient memory."``
    """
    text = message or ""
    if _NO_TUN.search(text):
        return "Waiting for the VM's TUN device plugin, omnigent-tun-device-plugin in kube-system"
    if re.search(r"insufficient memory", text, re.I):
        return "Waiting for memory: the VM doesn't have enough free memory for this sandbox"
    if re.search(r"insufficient cpu", text, re.I):
        return "Waiting for CPU: the VM doesn't have enough free CPU for this sandbox"
    if re.search(r"ephemeral-storage|disk ?pressure", text, re.I):
        return "Waiting for disk space on the VM"
    if re.search(r"unbound immediate PersistentVolumeClaims|volume", text, re.I):
        return "Waiting for the sandbox's home volume"
    return f"Waiting for room on the VM: {text}".rstrip(": ")


def plain_image_problem(reason: str | None, message: str | None) -> str:
    """
    Say why the runner image isn't there, from the kubelet's waiting reason.

    :param reason: e.g. ``"ImagePullBackOff"``.
    :param message: The kubelet's message.
    """
    text = f"{reason or ''} {message or ''}"
    if re.search(r"\b401\b|unauthorized|authentication required|denied", text, re.I):
        return "The registry refused to send the runner image (unauthorized)"
    if re.search(r"not found|manifest unknown|\b404\b", text, re.I):
        return "The runner image isn't in the registry"
    if reason == "InvalidImageName":
        return "The runner image's name is invalid"
    return "Retrying the runner image download"


def observe_pod(session_id: str | None, pod: object) -> None:
    """
    Report what a poll of the sandbox's Pod shows.

    :param session_id: Session/conversation identifier, or ``None``.
    :param pod: A ``V1Pod`` (or anything with the same attributes).
    """
    if not session_id:
        return
    with _lock:
        record = _records.get(session_id)
        if record is None or record.outcome != "running":
            return
        now = _now()
        changed = _set(record, "sandbox", "done", now=now)
        status = _get(pod, "status")
        scheduled = None
        for condition in _get(status, "conditions") or []:
            if _get(condition, "type") == "PodScheduled":
                scheduled = condition
        node = _get(pod, "spec", "node_name")
        if scheduled is not None and _get(scheduled, "status") == "True":
            changed |= _set(record, "schedule", "done", f"on {node}" if node else None, now=now)
        elif scheduled is not None and _get(scheduled, "reason") == "Unschedulable":
            changed |= _set(
                record,
                "schedule",
                "running",
                plain_unschedulable(_get(scheduled, "message")),
                tone="warning",
                now=now,
            )
        else:
            changed |= _set(record, "schedule", "running", now=now)
        init = _container(_get(status, "init_container_statuses"), "workspace-prep")
        host = _container(_get(status, "container_statuses"), "host")
        problem = None
        for container in (init, host):
            reason = _get(container, "state", "waiting", "reason")
            if reason in ("ErrImagePull", "ImagePullBackOff", "InvalidImageName"):
                problem = plain_image_problem(reason, _get(container, "state", "waiting", "message"))
        if problem is not None:
            changed |= _set(record, "image", "running", problem, tone="warning", now=now)
        elif _started(init) or _started(host):
            changed |= _set(record, "image", "done", now=now)
        elif record.steps.get("schedule") and record.steps["schedule"].state == "done":
            changed |= _set(record, "image", "running", now=now)
        clone = record.steps.get("clone")
        if clone is not None and init is not None:
            if _get(init, "state", "running"):
                changed |= _set(record, "clone", "running", now=now)
            elif _get(init, "state", "terminated", "exit_code") == 0:
                changed |= _set(record, "clone", "done", now=now)
        if _get(host, "state", "running"):
            changed |= _set(record, "host", "running", "Connecting to Omnigent", now=now)
    if changed:
        _publish(session_id)


_PULLED = re.compile(r"in ([0-9.]+)s")


def observe_events(session_id: str | None, events: Iterable[object]) -> None:
    """
    Log the Kubernetes events of the sandbox and its Pod, once each.

    Image events also fill in the image step's detail.

    :param session_id: Session/conversation identifier, or ``None``.
    :param events: ``V1Event`` objects (or dicts) for the sandbox's name.
    """
    if not session_id:
        return
    changed = False
    with _lock:
        record = _records.get(session_id)
        if record is None:
            return
        now = _now()
        ordered = sorted(
            events,
            key=lambda e: _timestamp(_get(e, "last_timestamp") or _get(e, "event_time")) or 0,
        )
        for event in ordered:
            reason = _get(event, "reason") or ""
            message = _get(event, "message") or ""
            key = f"{_get(event, 'metadata', 'uid')}:{_get(event, 'count')}:{reason}:{message}"
            if key in record.seen_events:
                continue
            record.seen_events.add(key)
            at = _timestamp(_get(event, "last_timestamp") or _get(event, "event_time")) or now
            if at < record.started_at - 5:
                continue
            level = "warning" if _get(event, "type") == "Warning" else "info"
            kind = _get(event, "involved_object", "kind") or ""
            prefix = f"{kind} " if kind and kind != "Pod" else ""
            _append_log(record, "k8s", f"{prefix}{reason}: {message}".strip(), level, at)
            if record.outcome != "running":
                continue
            if reason == "Pulling":
                changed |= _set(record, "image", "running", "Downloading the runner image", now=now)
            elif reason == "Pulled":
                if "already present" in message:
                    detail = "Already on the VM"
                else:
                    match = _PULLED.search(message)
                    detail = f"Downloaded in {float(match.group(1)):.0f}s" if match else "Downloaded"
                changed |= _set(record, "image", "done", detail, now=now)
    if changed:
        _publish(session_id)


def _timestamp(value: object) -> float | None:
    stamp = getattr(value, "timestamp", None)
    if callable(stamp):
        try:
            return float(stamp())
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def observe_container_log(session_id: str | None, container: str, text: str) -> None:
    """
    Log the new lines of a container's log, given the whole log so far.

    The init container's ``omnigent-prep:`` lines move each repository's clone.

    :param session_id: Session/conversation identifier, or ``None``.
    :param container: ``"workspace-prep"`` or ``"host"``.
    :param text: The container's log, as Kubernetes returns it.
    """
    if not session_id or not text:
        return
    source = "clone" if container == "workspace-prep" else "host"
    changed = False
    with _lock:
        record = _records.get(session_id)
        if record is None:
            return
        lines = text.splitlines()
        seen = record.log_offsets.get(container, 0)
        if seen > len(lines):
            # The container restarted: its log starts over.
            seen = 0
        record.log_offsets[container] = len(lines)
        now = _now()
        for line in lines[seen:]:
            if source == "clone" and line.startswith(PREP_MARKER):
                verb, _, name = line[len(PREP_MARKER) :].strip().partition(" ")
                state = {"cloning": "running", "cloned": "done", "failed": "failed", "kept": "skipped"}.get(verb)
                if state and name and record.outcome == "running":
                    detail = "kept from last time" if verb == "kept" else None
                    changed |= _set(record, "clone", state, detail, child=name, now=now)
                continue
            level = "error" if re.match(r"\s*(fatal|error):", line, re.I) else "info"
            _append_log(record, source, line, level, now)
    if changed:
        _publish(session_id)


# ── failures in plain words ─────────────────────────────

_PREFIXES = re.compile(
    r"^(managed sandbox host startup failed|managed sandbox launch failed|"
    r"managed host wake failed|The session's managed sandbox failed to launch):\s*",
    re.I,
)
_LOG_TAIL = re.compile(r"Container '([\w-]+)' log tail: (.*?)(?: Inspect with `kubectl|$)", re.S)
_INSPECT = re.compile(r"\s*Inspect with `kubectl[^`]*`\.?\s*$")


def _strip(error: str) -> str:
    text = error.strip()
    while True:
        stripped = _PREFIXES.sub("", text)
        if stripped == text:
            return _INSPECT.sub("", text).strip()
        text = stripped


def _git_lines(log: str) -> list[str]:
    return [
        line.strip()
        for line in log.splitlines()
        if re.match(r"\s*(fatal|error|remote):", line, re.I)
    ]


def explain_clone(log: str) -> dict[str, str]:
    """
    Say why the clone failed, from the init container's log.

    :param log: The ``workspace-prep`` container's log tail.
    :returns: ``{"title", "body", "child"?}``.
    """
    failed = [
        line[len(PREP_MARKER) :].strip().partition(" ")[2]
        for line in log.splitlines()
        if line.startswith(f"{PREP_MARKER} failed ")
    ]
    name = failed[0] if failed else None
    what = name or "a repository"
    git = " ".join(_git_lines(log))
    branch = re.search(r"Remote branch (\S+) not found", git)
    if re.search(r"Authentication failed|Invalid username or (password|token)|could not read Username|"
                 r"terminal prompts disabled|\b403\b|Permission to .* denied", git, re.I):
        body = (
            f"GitHub refused the Git token for {what}. The repository's own token in "
            "Settings → Repository variables may have expired, or the GitHub App can't "
            "reach this repository."
        )
    elif branch:
        body = f"The branch {branch.group(1)} doesn't exist in {what}."
    elif re.search(r"Repository not found|not found|does not appear to be a git repository", git, re.I):
        body = f"Git couldn't find {what}, or the token can't see it."
    elif re.search(r"Could not resolve host|unable to access|Connection timed out|Failed to connect", git, re.I):
        body = f"The sandbox couldn't reach the Git server for {what}."
    elif git:
        body = f"Git stopped with an error while cloning {what}."
    else:
        body = f"The workspace couldn't be prepared for {what}."
    result = {"title": f"Couldn't clone {name}" if name else "Couldn't clone the repositories", "body": body}
    if name:
        result["child"] = name
    return result


def explain(error: str) -> dict[str, str]:
    """
    Turn a launch error into a title and a sentence a person can act on.

    :param error: The error upstream reports, e.g. ``"managed sandbox host startup
        failed: Kubernetes sandbox pod 'x' did not start within 300s (...)"``.
    :returns: ``{"step", "title", "body"}``, plus ``"log"`` and ``"source"``
        when the error carries a container's log, and ``"child"`` for a
        repository that failed to clone.
    """
    text = _strip(error)
    result: dict[str, str]
    tail = _LOG_TAIL.search(text)
    log_text = tail.group(2).strip() if tail else ""
    container = tail.group(1) if tail else None
    summary = _LOG_TAIL.sub("", text).strip()
    summary = re.sub(r"\s*Recent events:.*$", "", summary, flags=re.S).strip()
    if container == "workspace-prep" or "workspace prep failed" in text:
        result = {"step": "clone", **explain_clone(log_text)}
    elif re.search(r"Unschedulable", text):
        message = re.search(r"Unschedulable: ([^)]*)", text)
        reason = message.group(1) if message else None
        if _NO_TUN.search(reason or ""):
            result = {
                "step": "schedule",
                "title": "No TUN device",
                "body": plain_unschedulable(reason) + ". `mise run status` shows why it isn't running.",
            }
        else:
            result = {
                "step": "schedule",
                "title": "No room on the VM",
                "body": plain_unschedulable(reason) + ". Stop a sandbox you aren't using, then try again.",
            }
    elif re.search(r"ErrImagePull|ImagePullBackOff|InvalidImageName", text):
        reason = re.search(r"(ErrImagePull|ImagePullBackOff|InvalidImageName)[: ]*([^;)]*)", text)
        result = {
            "step": "image",
            "title": "Couldn't get the runner image",
            "body": plain_image_problem(
                reason.group(1) if reason else None, reason.group(2) if reason else None
            )
            + ". The deployment's image pull settings may need fixing.",
        }
    elif re.search(r"CreateContainerConfigError|CreateContainerError|RunContainerError", text):
        secret = re.search(r'secret "([^"]+)" not found', text)
        result = {
            "step": "sandbox",
            "title": "Kubernetes couldn't create the sandbox's container",
            "body": (
                f"The Secret {secret.group(1)} it needs is missing. Try again to recreate it."
                if secret
                else "Its configuration was rejected. Try again; if it keeps failing, the "
                "details below say why."
            ),
        }
    elif re.search(r"OOMKilled", text):
        result = {
            "step": "host",
            "title": "The sandbox ran out of memory while starting",
            "body": "The host was stopped for using more memory than the sandbox has.",
        }
    elif re.search(r"crash-looping|exited before coming online", text):
        result = {
            "step": "host",
            "title": "The host keeps crashing while it starts",
            "body": "Its log below says why.",
        }
    elif re.search(r"did not create a child pod|exceeded quota|forbidden", text, re.I):
        result = {
            "step": "sandbox",
            "title": "Kubernetes didn't create the sandbox's Pod",
            "body": (
                "Too many sandboxes are running. Stop one you aren't using, then try again."
                if re.search(r"exceeded quota", text, re.I)
                else "The details below say what Kubernetes reported."
            ),
        }
    elif re.search(r"did not start within", text):
        result = {
            "step": "host",
            "title": "The sandbox took too long to start",
            "body": "Kubernetes didn't get its Pod running in time. The details below say "
            "what it was waiting for.",
        }
    elif re.search(r"did not come online|did not reconnect", text):
        result = {
            "step": "host",
            "title": "The host started but never connected",
            "body": "The Pod is running, but Omnigent's host inside it didn't connect back "
            "to the server in time.",
        }
    elif re.search(r"exited while starting|runner did not connect|runner process exited", text):
        result = {
            "step": "agent",
            "title": "The agent couldn't start",
            "body": "Its process exited before connecting.",
        }
    elif re.search(r"harness not configured|harness_not_configured", text):
        result = {
            "step": "agent",
            "title": "This agent isn't installed in the runner image",
            "body": "Pick another agent, or add it to the runner image.",
        }
    elif re.search(r"deleted while", text):
        result = {
            "step": "sandbox",
            "title": "The session was deleted",
            "body": "It was deleted while its sandbox was starting.",
        }
    else:
        result = {
            "step": "",
            "title": "The sandbox couldn't start",
            "body": summary or "No reason was given.",
        }
    if log_text and container:
        result["log"] = log_text
        result["source"] = container
    elif result["step"] == "agent":
        runner_log = re.search(r"exited while starting: (.*)$", text, re.S)
        if runner_log:
            result["log"] = runner_log.group(1).strip()
            result["source"] = "runner"
    return result
