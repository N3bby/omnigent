"""``omnigent-mcp``: serve the MCP over stdio, or sign in for it.

    omnigent-mcp                  serve (what Hermes runs)
    omnigent-mcp login --server https://omni.example.com [--username me]
    omnigent-mcp status           check the sign-in
    omnigent-mcp logout           forget the sign-in, here and on the server
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

import httpx

from .auth import (
    AuthError,
    TokenSource,
    credentials_path,
    load_credentials,
    login,
    normalize_server,
    revoke,
    save_credentials,
)


async def _login(server: str, username: str, password: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        creds = await login(client, server, username, password)
    path = save_credentials(creds)
    print(f"Signed in to {creds.server} as {creds.username}. Saved to {path}.")
    print("It lasts as long as the server allows: 30 days unless it's set longer.")


async def _status() -> None:
    creds = load_credentials()
    async with httpx.AsyncClient(timeout=30) as client:
        await TokenSource(client, creds).token()
    who = f" as {creds.username}" if creds.username else ""
    print(f"Signed in to {creds.server}{who}.")


async def _logout() -> None:
    creds = load_credentials()
    async with httpx.AsyncClient(timeout=30) as client:
        await revoke(client, creds)
    credentials_path().unlink(missing_ok=True)
    print(f"Signed out of {creds.server}.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="omnigent-mcp", description="An MCP server for an Omnigent deployment's sessions."
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="serve the MCP over stdio (the default)")
    login_cmd = commands.add_parser("login", help="sign in and keep a refresh token")
    login_cmd.add_argument("--server", required=True, help="such as https://omni.example.com")
    login_cmd.add_argument("--username", help="asked for when left out")
    commands.add_parser("status", help="check that the sign-in still works")
    commands.add_parser("logout", help="forget the sign-in, here and on the server")
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        # Imported here so signing in works without the MCP SDK's start-up cost.
        from .server import serve

        serve()
        return
    try:
        if args.command == "login":
            server = normalize_server(args.server)
            username = args.username or input("Omnigent username: ").strip()
            password = os.environ.get("OMNIGENT_PASSWORD") or getpass.getpass(
                f"Password for {username} on {server}: "
            )
            asyncio.run(_login(server, username, password))
        elif args.command == "status":
            asyncio.run(_status())
        elif args.command == "logout":
            asyncio.run(_logout())
    except (AuthError, httpx.HTTPError) as exc:
        print(f"omnigent-mcp: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
