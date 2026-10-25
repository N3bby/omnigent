"""What the MCP's tools do, on top of Omnigent's REST API.

Each method returns plain JSON-able data for the agent to read and raises
:class:`OmnigentError` with a message the agent can act on. ``server.py``
registers them as MCP tools; the tests drive them against a fake Omnigent.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from .api import MESSAGE_TIMEOUT, OmnigentApi, OmnigentError

# A project whose sessions default to a sandbox stores this as its host.
SANDBOX_HOST = "__sandbox__"
DEFAULT_AGENT = "claude-native-ui"
# Names people use for the agents the new-session picker shows.
AGENT_ALIASES = {
    "claude": "claude-native-ui",
    "claudecode": "claude-native-ui",
    "codex": "codex-native-ui",
}
# A project that only exists as a label on its sessions, from before projects
# had their own settings.
PROJECT_LABEL = "omni_project"

# How long sending a message waits for the agent to get it before answering
# that it's queued: a sandbox that's starting holds it for a minute or more.
DELIVERY_WAIT_SECONDS = 15.0
MAX_WAIT_SECONDS = 900
# How long a turn that has started may sit without a reply and not busy
# before waiting takes it as over.
SETTLE_SECONDS = 10.0
MAX_TRANSCRIPT_SCAN = 5000
_PAGE = 200
_BUSY = ("running", "waiting", "launching")


def iso(ts: Any) -> str | None:
    if not isinstance(ts, (int, float)):
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def truncate(text: str, limit: int) -> str:
    if limit > 0 and len(text) > limit:
        return f"{text[:limit]}… [{len(text) - limit} more characters]"
    return text


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif kind in ("input_image", "image"):
            parts.append("[image]")
        elif kind in ("input_file", "file"):
            parts.append(f"[file {block.get('filename') or block.get('file_id') or ''}]".strip())
    return "\n".join(parts)


def summarize_item(item: dict[str, Any], *, include_tools: bool, max_chars: int) -> dict | None:
    """One transcript entry for the agent, or ``None`` to leave the item out."""
    kind = item.get("type")
    entry: dict[str, Any] = {"id": item.get("id"), "at": iso(item.get("created_at"))}
    if kind == "message":
        if item.get("is_meta"):
            return None
        entry["role"] = item.get("role")
        entry["text"] = truncate(message_text(item.get("content")), max_chars)
        if item.get("created_by"):
            entry["by"] = item["created_by"]
        if item.get("interrupted"):
            entry["interrupted"] = True
        return entry
    if kind == "error":
        entry["role"] = "error"
        entry["text"] = truncate(str(item.get("message") or item.get("code") or "error"), max_chars)
        return entry
    if not include_tools:
        return None
    if kind == "function_call":
        entry.update(role="tool_call", tool=item.get("name"))
        entry["text"] = truncate(str(item.get("arguments") or ""), max_chars)
        return entry
    if kind == "function_call_output":
        entry["role"] = "tool_result"
        entry["text"] = truncate(str(item.get("output") or ""), max_chars)
        return entry
    if kind == "native_tool":
        inner = item.get("item") if isinstance(item.get("item"), dict) else {}
        entry.update(role="tool_call", tool=inner.get("name") or inner.get("type"))
        entry["text"] = truncate(json.dumps(inner, ensure_ascii=False), max_chars)
        return entry
    return None


def repo_workspace(repo: Any) -> str | None:
    """A project's ``{url, branch}`` as the ``<url>[#<branch>]`` sandboxes clone."""
    if isinstance(repo, str):
        return repo.strip() or None
    if not isinstance(repo, dict) or not isinstance(repo.get("url"), str):
        return None
    url = repo["url"].strip()
    branch = repo.get("branch") if isinstance(repo.get("branch"), str) else ""
    if not url:
        return None
    return f"{url}#{branch.strip()}" if branch.strip() else url


def _key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


class SessionTools:
    def __init__(self, api: OmnigentApi, *, poll_seconds: float = 3.0) -> None:
        self.api = api
        self.poll_seconds = poll_seconds
        # Messages still waiting for a sandbox to start, and why one failed.
        self._deliveries: dict[str, asyncio.Task] = {}
        self._delivery_errors: dict[str, str] = {}

    def session_url(self, session_id: str) -> str:
        return f"{self.api.server}/c/{quote(session_id)}"

    # ── Lookups ───────────────────────────────────────────────────

    async def _agents(self) -> list[dict[str, Any]]:
        data = await self.api.request("GET", "/v1/agents")
        return [a for a in data.get("data", []) if isinstance(a, dict)]

    async def _projects(self) -> list[dict[str, Any]]:
        """The caller's projects, in their sidebar order, with their settings."""
        summaries = await self.api.request("GET", "/v1/sessions/projects")
        full = await self.api.request("GET", "/v1/projects")
        by_id = {p["id"]: p for p in full.get("data", []) if isinstance(p, dict)}
        projects = []
        for summary in summaries if isinstance(summaries, list) else []:
            project = by_id.pop(summary.get("id"), None) or {}
            projects.append(
                {
                    "id": summary.get("id"),
                    "name": summary.get("name"),
                    "config": project.get("config") or {},
                }
            )
        projects.extend(
            {"id": p["id"], "name": p.get("name"), "config": p.get("config") or {}}
            for p in by_id.values()
        )
        return projects

    async def _resolve_project(self, project: str) -> dict[str, Any]:
        projects = await self._projects()
        wanted = project.strip()
        for match in (
            lambda p: p["id"] == wanted,
            lambda p: p["name"] == wanted,
            lambda p: str(p["name"]).lower() == wanted.lower(),
        ):
            found = [p for p in projects if match(p)]
            if len(found) == 1:
                return found[0]
        names = ", ".join(repr(p["name"]) for p in projects) or "none"
        raise OmnigentError(f"No project {project!r}. Your projects: {names}.")

    def _match_agent(self, agents: list[dict[str, Any]], agent: str) -> dict[str, Any]:
        wanted = agent.strip()
        key = _key(wanted)
        alias = AGENT_ALIASES.get(key)
        for match in (
            lambda a: a.get("id") == wanted,
            lambda a: a.get("name") == wanted,
            lambda a: alias is not None and a.get("name") == alias,
            lambda a: _key(str(a.get("name"))) == key,
            lambda a: _key(str(a.get("name")).removesuffix("-native-ui")) == key,
        ):
            found = [a for a in agents if match(a)]
            if found:
                return found[0]
        names = ", ".join(sorted(str(a.get("name")) for a in agents))
        raise OmnigentError(f"No agent {agent!r}. Agents: {names}.")

    def _agent_name(self, agents: list[dict[str, Any]], agent_id: Any) -> str | None:
        for a in agents:
            if a.get("id") == agent_id:
                return a.get("name")
        return None

    # ── Projects and agents ───────────────────────────────────────

    async def list_projects(self) -> dict[str, Any]:
        projects, agents = await asyncio.gather(self._projects(), self._agents())
        out = []
        for p in projects:
            config = p["config"] if isinstance(p["config"], dict) else {}
            host = config.get("host_id")
            entry: dict[str, Any] = {"id": p["id"], "name": p["name"]}
            if isinstance(config.get("icon"), str):
                entry["icon"] = config["icon"]
            entry["default_host"] = (
                "sandbox" if host in (None, SANDBOX_HOST) else f"connected host {host}"
            )
            if config.get("agent_id"):
                entry["default_agent"] = (
                    self._agent_name(agents, config["agent_id"]) or config["agent_id"]
                )
            if isinstance(config.get("model"), str):
                entry["default_model"] = config["model"]
            repos = [
                w for w in (repo_workspace(r) for r in config.get("sandbox_repos") or []) if w
            ]
            if repos:
                entry["repositories"] = repos
            if isinstance(config.get("workspace"), str):
                entry["workspace"] = config["workspace"]
            if p["id"] is None:
                entry["note"] = "Only a label on its sessions: it has no settings of its own."
            out.append(entry)
        return {"projects": out}

    async def list_agents(self) -> dict[str, Any]:
        agents = await self._agents()
        return {
            "default": DEFAULT_AGENT,
            "agents": [
                {
                    "name": a.get("name"),
                    "harness": a.get("harness"),
                    **({"description": a["description"]} if a.get("description") else {}),
                }
                for a in agents
            ],
        }

    # ── Reading sessions ──────────────────────────────────────────

    async def list_sessions(
        self,
        project: str | None = None,
        without_project: bool = False,
        archived: str = "exclude",
        search: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
        sort: str = "updated",
    ) -> dict[str, Any]:
        if archived not in ("exclude", "include", "only"):
            raise OmnigentError("archived must be 'exclude', 'include' or 'only'.")
        if sort not in ("updated", "created"):
            raise OmnigentError("sort must be 'updated' or 'created'.")
        params: dict[str, Any] = {
            "limit": max(1, min(limit, 100)),
            "after": cursor or None,
            "order": "desc",
            "sort_by": f"{sort}_at",
            "search_query": search or None,
        }
        if archived == "only":
            params["visibility"] = "archived"
        elif archived == "include":
            params["include_archived"] = "true"
        if without_project:
            params["project"] = ""
        elif project:
            # The API filters by the project's name.
            params["project"] = (await self._resolve_project(project))["name"]
        page = await self.api.request("GET", "/v1/sessions", params=params)
        sessions = []
        for s in page.get("data", []):
            labels = s.get("labels") if isinstance(s.get("labels"), dict) else {}
            entry = {
                "id": s.get("id"),
                "title": s.get("title"),
                "status": s.get("status"),
                "agent": s.get("agent_name"),
                "archived": bool(s.get("archived")),
                "created_at": iso(s.get("created_at")),
                "updated_at": iso(s.get("updated_at")),
                "url": self.session_url(s.get("id", "")),
            }
            if project:
                entry["project"] = params["project"]
            elif labels.get(PROJECT_LABEL):
                entry["project"] = labels[PROJECT_LABEL]
            if s.get("pending_elicitations_count"):
                entry["needs_response"] = True
            sessions.append(entry)
        result: dict[str, Any] = {"sessions": sessions}
        if page.get("has_more"):
            result["next_cursor"] = page.get("last_id")
        return result

    async def get_session(self, session_id: str) -> dict[str, Any]:
        s = await self.api.request("GET", f"/v1/sessions/{quote(session_id)}")
        result: dict[str, Any] = {
            "id": s.get("id"),
            "title": s.get("title"),
            "status": s.get("status"),
            "agent": s.get("agent_name"),
            "harness": s.get("harness"),
            "model": s.get("model_override") or s.get("llm_model"),
            "reasoning_effort": s.get("reasoning_effort"),
            "archived": bool(s.get("archived")),
            "created_at": iso(s.get("created_at")),
            "updated_at": iso(s.get("updated_at")),
            "url": self.session_url(s.get("id", session_id)),
        }
        if s.get("project_id"):
            names = {p["id"]: p["name"] for p in await self._projects()}
            result["project"] = names.get(s["project_id"], s["project_id"])
        else:
            labels = s.get("labels") if isinstance(s.get("labels"), dict) else {}
            if labels.get(PROJECT_LABEL):
                result["project"] = labels[PROJECT_LABEL]
        for key in ("workspace", "git_branch", "host_id", "sandbox_status", "total_cost_usd"):
            if s.get(key) is not None:
                result[key] = s[key]
        if s.get("last_task_error"):
            result["last_error"] = s["last_task_error"]
        if s.get("pending_elicitations"):
            result["needs_response"] = s["pending_elicitations"]
        if s.get("todos"):
            result["todos"] = s["todos"]
        if s.get("parent_session_id"):
            result["parent_session_id"] = s["parent_session_id"]
        self._add_delivery_state(session_id, result)
        return result

    async def read_session(
        self,
        session_id: str,
        limit: int = 20,
        older_than: str | None = None,
        include_tools: bool = False,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 200))
        collected: list[dict[str, Any]] = []
        cursor = older_than or None
        scanned = 0
        more = True
        # Newest first, so the latest entries come back however long it is.
        while more and len(collected) < limit and scanned < MAX_TRANSCRIPT_SCAN:
            page = await self.api.request(
                "GET",
                f"/v1/sessions/{quote(session_id)}/items",
                params={"order": "desc", "limit": _PAGE, "after": cursor},
            )
            items = page.get("data", [])
            more = bool(page.get("has_more"))
            for index, item in enumerate(items):
                scanned += 1
                cursor = item.get("id")
                entry = summarize_item(item, include_tools=include_tools, max_chars=max_chars)
                if entry is not None:
                    collected.append(entry)
                if len(collected) >= limit:
                    more = more or index < len(items) - 1
                    break
            if not items:
                break
        result: dict[str, Any] = {"session_id": session_id, "entries": list(reversed(collected))}
        if more and cursor:
            result["older_than"] = cursor
        return result

    # ── Starting and talking to sessions ──────────────────────────

    async def create_session(
        self,
        message: str | None = None,
        project: str | None = None,
        agent: str | None = None,
        repositories: list[str] | None = None,
        title: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        agents = await self._agents()
        body: dict[str, Any] = {}
        config: dict[str, Any] = {}
        proj: dict[str, Any] | None = None
        if project:
            proj = await self._resolve_project(project)
            config = proj["config"] if isinstance(proj["config"], dict) else {}
            if proj["id"] is not None:
                body["project_id"] = proj["id"]
            else:
                body["labels"] = {PROJECT_LABEL: proj["name"]}

        if agent:
            chosen = self._match_agent(agents, agent)
            body["agent_id"] = chosen["id"]
            agent_name = chosen.get("name")
        elif config.get("agent_id") and "project_id" in body:
            # The server fills in the project's agent, and its model goes with it.
            agent_name = self._agent_name(agents, config["agent_id"]) or config["agent_id"]
            if model is None and isinstance(config.get("model"), str):
                model = config["model"]
        else:
            chosen = self._match_agent(agents, DEFAULT_AGENT)
            body["agent_id"] = chosen["id"]
            agent_name = chosen.get("name")

        host = config.get("host_id")
        if host not in (None, "", SANDBOX_HOST):
            # The project starts its sessions on one of your own machines,
            # in its working directory, which the server fills in.
            if repositories:
                raise OmnigentError(
                    f"Project {proj and proj['name']!r} starts sessions on a connected host, "
                    "not a sandbox, so it can't clone repositories."
                )
            body["host_id"] = host
            where = f"connected host {host}"
            clones: list[str] = []
        else:
            if repositories is not None:
                clones = [r.strip() for r in repositories if r and r.strip()]
            else:
                clones = [
                    w for w in (repo_workspace(r) for r in config.get("sandbox_repos") or []) if w
                ]
            body["host_type"] = "managed"
            body["workspaces"] = clones
            if "project_id" in body:
                # Keep the server from filling in a project's working
                # directory or worktree, which only a connected host has.
                body["workspace"] = None
                body["git"] = None
            where = "sandbox"
        if title:
            body["title"] = title
        if model:
            body["model_override"] = model
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        session = await self.api.request("POST", "/v1/sessions", json=body)
        session_id = session["id"]
        result: dict[str, Any] = {
            "session_id": session_id,
            "url": self.session_url(session_id),
            "agent": session.get("agent_name") or agent_name,
            "runs_on": where,
        }
        if proj is not None:
            result["project"] = proj["name"]
        if clones:
            result["repositories"] = clones
        if model:
            result["model"] = model
        if message:
            result.update(await self._deliver(session_id, message, since=None))
        else:
            result["message"] = "none sent: the session waits for its first message"
        return result

    async def send_message(self, session_id: str, message: str) -> dict[str, Any]:
        if not message.strip():
            raise OmnigentError("The message is empty.")
        latest = await self.api.request(
            "GET",
            f"/v1/sessions/{quote(session_id)}/items",
            params={"order": "desc", "limit": 1},
        )
        data = latest.get("data") or []
        since = data[0].get("id") if data else None
        result = {"session_id": session_id, "url": self.session_url(session_id)}
        result.update(await self._deliver(session_id, message, since=since))
        return result

    async def _deliver(self, session_id: str, text: str, *, since: str | None) -> dict[str, Any]:
        previous = self._deliveries.get(session_id)
        if previous is not None and not previous.done():
            raise OmnigentError(
                "An earlier message to this session is still waiting for its sandbox to start; "
                "wait for it with wait_for_reply first."
            )
        self._delivery_errors.pop(session_id, None)
        event = {
            "type": "message",
            "data": {"role": "user", "content": [{"type": "input_text", "text": text}]},
        }
        task = asyncio.create_task(
            self.api.request(
                "POST",
                f"/v1/sessions/{quote(session_id)}/events",
                json=event,
                timeout=MESSAGE_TIMEOUT,
            )
        )
        self._deliveries[session_id] = task
        task.add_done_callback(lambda t: self._delivery_done(session_id, t))
        result: dict[str, Any] = {"since": since or ""}
        try:
            await asyncio.wait_for(asyncio.shield(task), DELIVERY_WAIT_SECONDS)
        except asyncio.TimeoutError:
            result["message"] = (
                "queued: the session's sandbox is starting, and the agent gets the message "
                "once it's up, usually within a couple of minutes"
            )
            return result
        except OmnigentError as exc:
            raise OmnigentError(f"The message wasn't sent: {exc}") from exc
        result["message"] = "delivered"
        return result

    def _delivery_done(self, session_id: str, task: asyncio.Task) -> None:
        if self._deliveries.get(session_id) is task:
            self._deliveries.pop(session_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._delivery_errors[session_id] = str(exc)

    def _add_delivery_state(self, session_id: str, result: dict[str, Any]) -> None:
        task = self._deliveries.get(session_id)
        if task is not None and not task.done():
            result["message_delivery"] = "waiting for the sandbox to start"
        elif session_id in self._delivery_errors:
            result["message_delivery"] = f"failed: {self._delivery_errors[session_id]}"

    async def wait_for_reply(
        self,
        session_id: str,
        since: str | None = None,
        timeout_seconds: int = 120,
        max_chars: int = 8000,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(1, min(timeout_seconds, MAX_WAIT_SECONDS))
        replies: list[dict[str, Any]] = []
        cursor = since or None
        # When the turn showed up in the transcript, such as the message sent.
        # Until then the session's status is still the previous turn's.
        started_at: float | None = None
        while True:
            entries, new_cursor = await self._new_entries(session_id, cursor, max_chars)
            if new_cursor != cursor and started_at is None:
                started_at = time.monotonic()
            cursor = new_cursor
            replies.extend(entries)
            if session_id in self._delivery_errors:
                return {
                    "status": "failed",
                    "error": f"The message wasn't sent: {self._delivery_errors[session_id]}",
                    "replies": replies,
                }
            snapshot = await self.api.request("GET", f"/v1/sessions/{quote(session_id)}")
            status = snapshot.get("status")
            delivering = session_id in self._deliveries
            result: dict[str, Any] = {
                "status": status,
                "replies": replies,
                "since": cursor or "",
                "url": self.session_url(session_id),
            }
            if snapshot.get("pending_elicitations"):
                result["status"] = "needs_response"
                result["needs_response"] = snapshot["pending_elicitations"]
                result["note"] = "The agent is waiting for an answer in Omnigent's web UI."
                return result
            idle = not delivering and status not in _BUSY
            answered = any(r.get("role") in ("assistant", "error") for r in replies)
            # A turn that ended without a word, such as one that failed: give
            # the status a moment to catch up with the transcript first.
            ended_quietly = (
                started_at is not None and time.monotonic() - started_at >= SETTLE_SECONDS
            )
            if idle and (answered or ended_quietly):
                if status == "failed" and snapshot.get("last_task_error"):
                    result["error"] = snapshot["last_task_error"]
                return result
            if time.monotonic() >= deadline:
                result["status"] = "still_working" if (delivering or status in _BUSY) else status
                result["note"] = (
                    "Timed out waiting. Call wait_for_reply again with this since to keep waiting."
                )
                return result
            await asyncio.sleep(self.poll_seconds)

    async def _new_entries(
        self, session_id: str, after: str | None, max_chars: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        """What the agent added after an item, and the last item read."""
        entries: list[dict[str, Any]] = []
        cursor = after
        for _ in range(MAX_TRANSCRIPT_SCAN // _PAGE):
            page = await self.api.request(
                "GET",
                f"/v1/sessions/{quote(session_id)}/items",
                params={"order": "asc", "limit": _PAGE, "after": cursor},
            )
            items = page.get("data", [])
            for item in items:
                cursor = item.get("id")
                entry = summarize_item(item, include_tools=False, max_chars=max_chars)
                # The transcript also has the message that was sent.
                if entry is not None and entry.get("role") != "user":
                    entries.append(entry)
            if not page.get("has_more") or not items:
                break
        return entries, cursor
