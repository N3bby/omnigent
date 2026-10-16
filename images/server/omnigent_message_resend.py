"""Deliver a message the web UI sends again only once.

The web UI gives each message a ``stable_id``. While a session's sandbox
starts, the server holds the message's ``POST /v1/sessions/{id}/events`` open
until the agent can take it, often for half a minute. If the connection drops
in that time, as it does on a phone when Tailscale reapplies its routes, the
server still delivers the message, but the browser only sees a network error.
The web UI then sends it again with the same ``stable_id``
(``images/server/web-patches/0024-resend-dropped-message.patch``), and so may
the user, from the composer. Upstream would deliver every copy: a Claude Code
session would get the message typed into its terminal twice.

:func:`once` runs one request per key. A copy that comes while the first is
still running waits for it and gets its answer or its error. A copy that comes
after it succeeded gets its answer straight away, for :data:`TTL_S`. After a
failure, the next copy runs again, since the message most likely didn't get
through. ``images/server/patches/0017-resend-dropped-message.patch`` calls it
for messages with a ``stable_id``, keyed by session, user, ``stable_id`` and
content.

Requests are tracked in memory, per server process; the deployment runs one.
Nothing here imports Omnigent, so the tests can load it on its own.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Hashable

TTL_S = 15 * 60
"""How long a delivered message's answer is kept for a copy of it."""
MAX_ANSWERS = 2000
"""Answers kept at most; the oldest go first."""

_STABLE_ID = re.compile(r"[0-9a-f]{32}")
"""The web UI's stable ids; upstream ignores any other value."""

_running: dict[Hashable, asyncio.Future[Any]] = {}
_answers: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()


def stable_id(data: object) -> str | None:
    """Return a message event's ``stable_id``, or ``None`` when it has none."""
    value = data.get("stable_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) and _STABLE_ID.fullmatch(value) else None


def content_key(data: object) -> str:
    """Return a message's content as a key, so an edited resend is delivered.

    A message put back in the composer keeps its ``stable_id`` even when the
    user changes it before sending it again.
    """
    content = data.get("content") if isinstance(data, dict) else None
    return json.dumps(content, sort_keys=True, default=str)


def _answer(key: Hashable) -> tuple[bool, Any]:
    now = time.monotonic()
    while _answers:
        oldest_key, (expires, _) = next(iter(_answers.items()))
        if expires > now:
            break
        del _answers[oldest_key]
    if key in _answers:
        return True, copy.deepcopy(_answers[key][1])
    return False, None


async def once(key: Hashable, request: Callable[[], Awaitable[Any]]) -> Any:
    """Run *request* unless a copy with the same *key* ran or is running.

    :param key: Identifies the message, e.g. its session, user, ``stable_id``
        and :func:`content_key`.
    :param request: Handles the message, e.g. upstream's ``_post_event_impl``.
    :returns: What the request returned, for this copy or the first.
    :raises Exception: The running first copy's error, for copies waiting on it.
    """
    while True:
        known, answer = _answer(key)
        if known:
            return answer
        running = _running.get(key)
        if running is None:
            break
        try:
            return copy.deepcopy(await asyncio.shield(running))
        except asyncio.CancelledError:
            # Only the first copy was cancelled: this one tries again.
            if not running.cancelled():
                raise
    future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    # Nobody may wait for it, so don't log an error no one retrieved.
    future.add_done_callback(lambda done: done.cancelled() or done.exception())
    _running[key] = future
    try:
        result = await request()
    except Exception as error:
        future.set_exception(error)
        raise
    else:
        _answers[key] = (time.monotonic() + TTL_S, copy.deepcopy(result))
        _answers.move_to_end(key)
        while len(_answers) > MAX_ANSWERS:
            _answers.popitem(last=False)
        future.set_result(result)
        return result
    finally:
        _running.pop(key, None)
        # Cancelled, or stopped by something that isn't an error: a copy
        # waiting for it runs instead.
        if not future.done():
            future.cancel()


def reset() -> None:
    """Forget every request; for tests."""
    _running.clear()
    _answers.clear()
