"""The MCP server: Omnigent's sessions and projects as tools, over stdio."""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from .api import OmnigentApi, OmnigentError
from .auth import AuthError, TokenSource, load_credentials
from .tools import SessionTools

INSTRUCTIONS = """\
Omnigent runs coding agents (Claude Code, Codex, Hermes) in sessions, each in
its own sandbox with the repositories it works on. Projects group sessions and
can set the repositories, agent and model new sessions in them start with.

Use these tools to look into the user's sessions, archived ones included, and
to start new sessions or send a message to one. Starting a session costs a
sandbox, so check for an existing session first when continuing earlier work.
A new session's sandbox takes a minute or two to start; wait_for_reply waits
for the agent's answer. Every session has a url the user can open."""


class _Lazy:
    """Signs in on the first tool call, so the server starts without a sign-in."""

    def __init__(self) -> None:
        self._tools: SessionTools | None = None

    def get(self) -> SessionTools:
        if self._tools is None:
            try:
                creds = load_credentials()
            except AuthError as exc:
                raise ToolError(str(exc)) from exc
            client = httpx.AsyncClient(headers={"User-Agent": "omnigent-mcp"})
            self._tools = SessionTools(OmnigentApi(client, TokenSource(client, creds)))
        return self._tools


async def _call(lazy: _Lazy, method: str, **kwargs: Any) -> dict[str, Any]:
    tools = lazy.get()
    try:
        return await getattr(tools, method)(**kwargs)
    except OmnigentError as exc:
        raise ToolError(str(exc)) from exc


def build_server() -> MCPServer:
    server = MCPServer(name="omnigent", instructions=INSTRUCTIONS)
    lazy = _Lazy()

    @server.tool()
    async def list_projects() -> dict[str, Any]:
        """List the user's Omnigent projects, with what new sessions in each start with.

        Shows each project's repositories (cloned into the sandbox), default agent
        and model, and whether its sessions run in a sandbox or on a connected host.
        """
        return await _call(lazy, "list_projects")

    @server.tool()
    async def list_agents() -> dict[str, Any]:
        """List the agents a session can run, such as claude-native-ui (Claude Code),
        codex-native-ui (Codex) and hermes."""
        return await _call(lazy, "list_agents")

    @server.tool()
    async def list_sessions(
        project: Annotated[
            str | None, Field(description="Only sessions in this project, by name or id.")
        ] = None,
        without_project: Annotated[
            bool, Field(description="Only sessions that aren't in any project.")
        ] = False,
        archived: Annotated[
            Literal["exclude", "include", "only"],
            Field(description="Leave out archived sessions, include them, or list only them."),
        ] = "exclude",
        search: Annotated[
            str | None,
            Field(description="Only sessions whose title or conversation contains this text."),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        cursor: Annotated[
            str | None, Field(description="next_cursor from the previous page.")
        ] = None,
        sort: Annotated[
            Literal["updated", "created"], Field(description="Newest by last activity or creation.")
        ] = "updated",
    ) -> dict[str, Any]:
        """List the user's Omnigent sessions, newest first: id, title, status, agent and url.

        Status is idle, running, waiting or failed. Use read_session for what was
        said in one.
        """
        return await _call(
            lazy,
            "list_sessions",
            project=project,
            without_project=without_project,
            archived=archived,
            search=search,
            limit=limit,
            cursor=cursor,
            sort=sort,
        )

    @server.tool()
    async def get_session(session_id: str) -> dict[str, Any]:
        """Get one session's details: status, agent, model, project, workspace and
        git branch, its last error, its todo list, and any question it's waiting on."""
        return await _call(lazy, "get_session", session_id=session_id)

    @server.tool()
    async def read_session(
        session_id: str,
        limit: Annotated[
            int, Field(ge=1, le=200, description="How many of the latest entries to return.")
        ] = 20,
        older_than: Annotated[
            str | None,
            Field(description="older_than from a previous call, to read further back."),
        ] = None,
        include_tools: Annotated[
            bool, Field(description="Also return the agent's tool calls and their results.")
        ] = False,
        max_chars: Annotated[
            int, Field(ge=200, le=50000, description="Cut each entry's text after this many.")
        ] = 4000,
    ) -> dict[str, Any]:
        """Read a session's conversation, archived sessions included: the latest
        entries, oldest of them first. Works without waking the session's sandbox."""
        return await _call(
            lazy,
            "read_session",
            session_id=session_id,
            limit=limit,
            older_than=older_than,
            include_tools=include_tools,
            max_chars=max_chars,
        )

    @server.tool()
    async def create_session(
        message: Annotated[
            str | None,
            Field(description="The first message for the agent. Without one the session waits."),
        ] = None,
        project: Annotated[
            str | None,
            Field(
                description="Start it in this project, by name or id, with the project's "
                "repositories, agent and model unless given here. Leave out for a session "
                "in no project."
            ),
        ] = None,
        agent: Annotated[
            str | None,
            Field(
                description="claude (Claude Code, the default), codex, hermes, or another "
                "name from list_agents."
            ),
        ] = None,
        repositories: Annotated[
            list[str] | None,
            Field(
                description="Git URLs to clone into the sandbox, each optionally with "
                "#<branch>, e.g. https://github.com/org/repo#main. Replaces the project's. "
                "Pass [] for an empty sandbox."
            ),
        ] = None,
        title: Annotated[
            str | None, Field(description="Otherwise Omnigent titles it from the conversation.")
        ] = None,
        model: Annotated[
            str | None, Field(description="A model for the agent, such as opus for Claude Code.")
        ] = None,
        reasoning_effort: Annotated[
            Literal["low", "medium", "high", "xhigh", "max"] | None, Field()
        ] = None,
    ) -> dict[str, Any]:
        """Start a new Omnigent session in a fresh sandbox, optionally in a project,
        and send it a first message.

        Returns the session's id and url. The sandbox takes a minute or two to
        start: the message is then 'queued' and reaches the agent once it's up.
        Call wait_for_reply with the session id and the returned since to get the
        agent's answer.
        """
        return await _call(
            lazy,
            "create_session",
            message=message,
            project=project,
            agent=agent,
            repositories=repositories,
            title=title,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    @server.tool()
    async def send_message(session_id: str, message: str) -> dict[str, Any]:
        """Send a message to an existing session's agent, as the user would in the chat.

        A session whose sandbox has stopped starts it again first. Call
        wait_for_reply with the returned since to get the answer.
        """
        return await _call(lazy, "send_message", session_id=session_id, message=message)

    @server.tool()
    async def wait_for_reply(
        session_id: str,
        since: Annotated[
            str | None,
            Field(
                description="since from create_session, send_message or a previous "
                "wait_for_reply: only what the agent said after it."
            ),
        ] = None,
        timeout_seconds: Annotated[
            int, Field(ge=1, le=900, description="Give up waiting after this long.")
        ] = 100,
    ) -> dict[str, Any]:
        """Wait until a session's agent has finished its turn and return what it said.

        Returns status 'idle' with the replies when done, 'needs_response' when the
        agent asks the user something in Omnigent, or 'still_working' on timeout:
        call again with the returned since to keep waiting.
        """
        return await _call(
            lazy,
            "wait_for_reply",
            session_id=session_id,
            since=since,
            timeout_seconds=timeout_seconds,
        )

    return server


def serve() -> None:
    # The MCP SDK logs at INFO to stderr, which Hermes keeps: leave out a line
    # for every request to Omnigent.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    build_server().run("stdio")
