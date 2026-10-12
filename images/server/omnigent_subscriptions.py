"""Named subscription credentials and atomic active-account projections.

Only active credentials are mounted in runners. Inactive credentials and names
stay in the control-plane Secret. Kubernetes resourceVersion prevents lost edits.
"""
from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

NAMESPACE = "omnigent-sandboxes"
SECRET = "omnigent-subscriptions"
MOUNT = "/run/omnigent/subscriptions"
PROVIDERS = ("claude", "codex")


def pod_volumes():
    keys = ("selection.json", "claude-token", "codex-auth.json")
    return ([{"name": "subscriptions", "secret": {
        "secretName": SECRET, "optional": True, "defaultMode": 0o440,
        "items": [{"key": key, "path": key} for key in keys],
    }}], [{"name": "subscriptions", "mountPath": MOUNT, "readOnly": True}])


def read(core: Any):
    try:
        secret = core.read_namespaced_secret(SECRET, NAMESPACE)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
        return {"accounts": [], "active": {}}, None
    return json.loads(base64.b64decode(secret.data["store.json"])), secret.metadata.resource_version


def summary(store):
    return {"data": [{k: a[k] for k in ("id", "provider", "name", "updated_at")} | {
        "active": store["active"].get(a["provider"]) == a["id"],
    } for a in store["accounts"]], "active": store["active"]}


def write(core, store, version):
    projection = {"claude-token": "", "codex-auth.json": ""}
    selection = {}
    for a in store["accounts"]:
        if store["active"].get(a["provider"]) == a["id"]:
            key = "claude-token" if a["provider"] == "claude" else "codex-auth.json"
            projection[key] = a["credential"]
            selection[a["provider"]] = {"id": a["id"], "revision": a["revision"]}
    projection["selection.json"] = json.dumps(selection)
    projection["store.json"] = json.dumps(store)
    if sum(len(value.encode()) for value in projection.values()) > 900 * 1024:
        raise ValueError("Saved credentials are too large. Remove an unused account first.")
    body = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
            "metadata": {"name": SECRET, "namespace": NAMESPACE},
            "data": {k: base64.b64encode(v.encode()).decode() for k, v in projection.items()}}
    if version:
        body["metadata"]["resourceVersion"] = version
        core.replace_namespaced_secret(SECRET, NAMESPACE, body)
    else:
        core.create_namespaced_secret(NAMESPACE, body)
    return summary(store)


def validate(provider, credential):
    if provider not in PROVIDERS:
        raise ValueError("Choose Claude or Codex.")
    credential = credential.strip()
    if len(credential) > 64 * 1024:
        raise ValueError("The credential is too large.")
    if provider == "claude":
        if not re.fullmatch(r"sk-ant-oat[0-9]+-[A-Za-z0-9_-]+", credential):
            raise ValueError("Paste a Claude subscription token from claude setup-token.")
    else:
        try:
            auth = json.loads(credential)
            tokens = auth["tokens"]
            if not isinstance(tokens, dict) or not all(isinstance(tokens.get(k), str) and tokens[k]
                    for k in ("access_token", "refresh_token", "id_token")):
                raise ValueError()
            if auth.get("auth_mode") not in (None, "chatgpt"):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ValueError("Upload a Codex auth.json from a ChatGPT subscription login.") from None
        credential = json.dumps(auth)
    return credential


def save(core, provider, name, credential=None, account_id=None, *, login_id=None):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Give the subscription a name of 1–80 characters.")
    if provider not in PROVIDERS:
        raise ValueError("Choose Claude or Codex.")
    store, version = read(core)
    if login_id:
        completed = next((a for a in store["accounts"] if a.get("login_id") == login_id), None)
        if completed is not None:
            return summary(store)
    existing = next((a for a in store["accounts"] if a["id"] == account_id), None)
    if account_id and not existing:
        raise ValueError("Subscription no longer exists. Reload the page.")
    if existing and existing["provider"] != provider:
        raise ValueError("A subscription's provider cannot be changed.")
    if any(a["provider"] == provider and a["name"].casefold() == name.casefold()
           and a is not existing for a in store["accounts"]):
        raise ValueError("That provider already has a subscription with this name.")
    if credential is not None:
        credential = validate(provider, credential)
    elif not existing:
        raise ValueError("Add credentials for the subscription.")
    if existing is None:
        if len(store["accounts"]) >= 32:
            raise ValueError("You can store up to 32 subscriptions.")
        existing = {"id": uuid4().hex, "provider": provider}
        store["accounts"].append(existing)
    if login_id:
        existing["login_id"] = login_id
    existing["name"] = name
    existing["updated_at"] = datetime.now(timezone.utc).isoformat()
    if credential is not None:
        existing["credential"] = credential
        existing["revision"] = uuid4().hex
    # Adding the first account is explicit; later additions keep the current choice.
    store["active"].setdefault(provider, existing["id"])
    return write(core, store, version)


def activate(core, account_id):
    store, version = read(core)
    account = next((a for a in store["accounts"] if a["id"] == account_id), None)
    if account is None:
        raise ValueError("Subscription no longer exists. Reload the page.")
    store["active"][account["provider"]] = account_id
    return write(core, store, version)


def delete(core, account_id):
    store, version = read(core)
    account = next((a for a in store["accounts"] if a["id"] == account_id), None)
    if account is None:
        raise ValueError("Subscription no longer exists. Reload the page.")
    if store["active"].get(account["provider"]) == account_id:
        raise ValueError("Switch to another subscription before removing the active one.")
    store["accounts"].remove(account)
    return write(core, store, version)
