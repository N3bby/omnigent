#!/usr/bin/env python3
"""Strict, dependency-free configuration loader shared by operator scripts."""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The server renews an active runner Pod's deadline about once a minute, so
# its window must cover a few missed renewals. Upstream's default.
MIN_RUNNER_SUSPEND_WINDOW_SECONDS = 300

# How Let's Encrypt checks that we control the hostname. http-01 fetches a
# token from the VM over the Internet; cloudflare-dns-01 looks up a TXT record
# that cert-manager writes through the Cloudflare API, so the VM can be private.
ACME_CHALLENGES = ("http-01", "cloudflare-dns-01")


class ConfigError(ValueError):
    pass


def load_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for number, raw in enumerate((ROOT / "versions.yaml").read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ConfigError(f"versions.yaml:{number}: expected key: value")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not value:
            raise ConfigError(f"versions.yaml:{number}: invalid entry")
        if key in result:
            raise ConfigError(f"versions.yaml:{number}: duplicate {key}")
        result[key] = value
    return result


def load_environment(name: str) -> dict[str, object]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ConfigError("environment must contain only lowercase letters, digits, _ or -")
    path = ROOT / "environments" / f"{name}.toml"
    if not path.is_file():
        raise ConfigError(f"missing environment file: {path}")
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    if any(isinstance(value, (dict, list)) for value in data.values()):
        raise ConfigError("environment values must be scalar")
    return data


def require_string(data: dict[str, object], key: str, pattern: str | None = None) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value or "\n" in value:
        raise ConfigError(f"{key} must be a non-empty single-line string")
    if pattern and not re.fullmatch(pattern, value):
        raise ConfigError(f"{key} has an invalid value: {value!r}")
    return value


def validate(name: str, *, require_digests: bool = True) -> tuple[dict[str, object], dict[str, str]]:
    data, versions = load_environment(name), load_versions()
    required_versions = {
        "omnigent", "omnigent_commit", "k3s", "k3s_installer_sha256",
        "cert_manager", "cert_manager_manifest_sha256", "agent_sandbox",
        "agent_sandbox_manifest_sha256", "postgres",
        "postgres_digest", "vault", "vault_digest", "hvac", "web_builder",
        "web_builder_digest", "pnpm", "claude_code", "codex_cli",
    }
    missing_versions = sorted(required_versions - versions.keys())
    if missing_versions:
        raise ConfigError(f"versions.yaml is missing: {', '.join(missing_versions)}")

    hostname = require_string(data, "hostname", r"(?=.{4,253}$)(?!-)[A-Za-z0-9.-]+(?<!-)")
    if "." not in hostname or hostname.endswith(".example.com"):
        raise ConfigError("hostname must be a real fully-qualified DNS name")
    for key in ("acme_email", "admin_email"):
        require_string(data, key, r"[^\s@]+@[^\s@]+\.[^\s@]+")
    data.setdefault("acme_challenge", "http-01")
    if data["acme_challenge"] not in ACME_CHALLENGES:
        raise ConfigError(f"acme_challenge must be one of: {', '.join(ACME_CHALLENGES)}")
    registry = require_string(data, "image_registry", r"[a-z0-9.-]+/[A-Za-z0-9_.-]+")
    if "REPLACE_ME" in registry:
        raise ConfigError("set image_registry to the public GHCR namespace")
    for key in ("runner_image", "server_image"):
        require_string(data, key, r"[A-Za-z0-9_.-]+")
    for key in (
        "server_cpu_request", "server_memory_request", "postgres_storage", "artifact_storage",
        "codex_storage", "runner_cpu_request", "runner_memory_request",
        "runner_ephemeral_request", "runner_cpu_limit", "runner_memory_limit",
        "runner_ephemeral_limit", "runner_home_limit",
    ):
        require_string(data, key, r"[0-9]+(?:m|Ki|Mi|Gi|Ti)?")
    for key in ("runner_pod_ready_timeout_seconds", "runner_max_concurrency"):
        if not isinstance(data.get(key), int) or int(data[key]) < 1:
            raise ConfigError(f"{key} must be a positive integer")
    for key in ("runner_agent_idle_seconds", "runner_idle_shutdown_seconds"):
        value = data.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 60:
            raise ConfigError(f"{key} must be an integer of at least 60")
    if runner_suspend_window_seconds(data) < MIN_RUNNER_SUSPEND_WINDOW_SECONDS:
        raise ConfigError(
            "runner_idle_shutdown_seconds must be at least "
            f"{MIN_RUNNER_SUSPEND_WINDOW_SECONDS} more than runner_agent_idle_seconds"
        )
    for key in ("codex_bypass_approvals", "claude_bypass_permissions", "backups_enabled"):
        if not isinstance(data.get(key), bool):
            raise ConfigError(f"{key} must be true or false")
    if data["backups_enabled"] is not False:
        raise ConfigError("backups_enabled must remain false; backups are intentionally unsupported")
    if data.get("runner_network_policy") != "unrestricted":
        raise ConfigError("runner_network_policy must be 'unrestricted'")
    for key in ("runner_digest", "server_digest"):
        value = require_string(data, key)
        if require_digests and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ConfigError(f"{key} is not locked; run ENV={name} mise run lock-images")
    data.setdefault("kubernetes_api_host", hostname)
    require_string(data, "kubernetes_api_host", r"(?=.{1,253}$)(?!-)[A-Za-z0-9.-]+(?<!-)")
    if "deploy_github_repository_id" in data:
        require_string(data, "deploy_github_repository_id", r"[1-9][0-9]*")
        data.setdefault("deploy_github_environment", "production")
        require_string(data, "deploy_github_environment", r"[A-Za-z0-9_.-]+")
    return data, versions


def runner_suspend_window_seconds(cfg: dict[str, object]) -> int:
    """How long a runner Pod stays up after its agent exits idle."""
    return int(cfg["runner_idle_shutdown_seconds"]) - int(cfg["runner_agent_idle_seconds"])


def deploy_audience(cfg: dict[str, object]) -> str:
    """The OIDC audience GitHub Actions requests and the API server accepts."""
    return f"https://{cfg['hostname']}/kubernetes"


def kubernetes_ca_path(name: str) -> Path:
    """The cluster CA that `mise run bootstrap` records for CI to pin."""
    return ROOT / "environments" / f"{name}.kubernetes-ca.crt"


def digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: object) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)
