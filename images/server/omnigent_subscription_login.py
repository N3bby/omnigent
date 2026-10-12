"""Short-lived subscription sign-in Pods, controlled by authenticated admins."""
import json
import os
import re
import threading
from uuid import uuid4

import omnigent_subscriptions as subscriptions

LABEL = "omnigent.dev/subscription-login"
PREFIX = "subscription-login-"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Claude's code is the authorization code and its state, joined by "#".
CLAUDE_CODE = re.compile(r"[A-Za-z0-9_-]+#[A-Za-z0-9_-]+")
_START_LOCK = threading.Lock()


def start(core, name, account_id=None, provider="codex"):
    # The deployment runs one server replica; serialize competing sign-in starts.
    with _START_LOCK:
        return _start(core, name, account_id, provider)


def _start(core, name, account_id=None, provider="codex"):
    if provider not in subscriptions.PROVIDERS:
        raise ValueError("Choose Claude or Codex.")
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Give the subscription a name of 1–80 characters.")
    store, _ = subscriptions.read(core)
    account = next((a for a in store["accounts"] if a["id"] == account_id), None)
    if account_id and (account is None or account["provider"] != provider):
        raise ValueError("Subscription no longer exists. Reload the page.")
    if any(a["provider"] == provider and a["name"].casefold() == name.casefold()
           and a["id"] != account_id for a in store["accounts"]):
        raise ValueError("That provider already has a subscription with this name.")
    image = os.environ.get("OMNIGENT_SUBSCRIPTION_LOGIN_IMAGE")
    if not image:
        raise ValueError("Device login is unavailable until the server configuration is updated.")
    if len(store["accounts"]) >= 32 and not account_id:
        raise ValueError("You can store up to 32 subscriptions.")
    # Bound helper count even when a browser disappears without cancelling.
    helpers = core.list_namespaced_pod(subscriptions.NAMESPACE, label_selector=LABEL).items
    for pod in helpers:
        if pod.status.phase in ("Succeeded", "Failed"):
            core.delete_namespaced_pod(pod.metadata.name, subscriptions.NAMESPACE)
    if sum(p.status.phase not in ("Succeeded", "Failed") for p in helpers) >= 3:
        raise ValueError("Three sign-ins are already in progress. Cancel one or wait for it to expire.")
    login_id = uuid4().hex
    core.create_namespaced_pod(subscriptions.NAMESPACE, {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": PREFIX + login_id, "labels": {LABEL: provider},
                     "annotations": {"omnigent.dev/subscription-name": name,
                                     "omnigent.dev/subscription-id": account_id or ""}},
        "spec": {"restartPolicy": "Never", "activeDeadlineSeconds": 900,
                 "automountServiceAccountToken": False, "hostUsers": False,
                 "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                     "seccompProfile": {"type": "RuntimeDefault"}},
                 "containers": [{"name": "login", "image": image,
                     "command": ["sh", "-c", "mkdir -p /tmp/codex-login; "
                         # As in setup-codex: its config stores the login in auth.json.
                         "cp /opt/codex-home/config.toml /tmp/codex-login/config.toml; "
                         "node /usr/local/bin/codex-real login --device-auth || exit $?; touch /tmp/login-done; sleep 900"]
                         if provider == "codex" else ["sh", "-c", "/opt/venv/bin/python /usr/local/lib/omnigent/subscription-login.py; sleep 900"],
                     "env": [{"name": "HOME", "value": "/home/omnigent"},
                             {"name": "CODEX_HOME", "value": "/tmp/codex-login"}],
                     "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                     "resources": {"requests": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": "64Mi"},
                                   "limits": {"cpu": "1", "memory": "1Gi", "ephemeral-storage": "128Mi"}}}]},
    })
    return {"id": login_id, "status": "starting"}


def pod_name(login_id):
    if not re.fullmatch(r"[0-9a-f]{32}", login_id):
        raise ValueError("Invalid sign-in request.")
    return PREFIX + login_id


def cancel(core, login_id):
    try:
        core.delete_namespaced_pod(pod_name(login_id), subscriptions.NAMESPACE)
    except Exception as exc:
        if getattr(exc, "status", None) != 404:
            raise
    return {"status": "cancelled"}


def poll(core, login_id, execute=None):
    name = pod_name(login_id)
    try:
        pod = core.read_namespaced_pod(name, subscriptions.NAMESPACE)
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            store, _ = subscriptions.read(core)
            if any(a.get("login_id") == login_id for a in store["accounts"]):
                return {"status": "complete", "subscriptions": subscriptions.summary(store)}
            return {"status": "expired"}
        raise
    if pod.status.phase in ("Succeeded", "Failed"):
        cancel(core, login_id)
        return {"status": "expired" if getattr(pod.status, "reason", None) == "DeadlineExceeded" else "failed"}
    if any(getattr(getattr(state, "state", None), "waiting", None)
           and getattr(state.state.waiting, "reason", None) in (
               "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError", "CrashLoopBackOff"
           ) for state in (getattr(pod.status, "container_statuses", None) or [])):
        cancel(core, login_id)
        return {"status": "failed"}
    if pod.status.phase != "Running":
        return {"status": "starting"}
    if execute is None:
        execute = lambda command: _execute(core, name, command)
    provider = (pod.metadata.labels or {}).get(LABEL, "codex")
    if provider == "claude":
        payload = execute(["/opt/venv/bin/python", "-c", "from pathlib import Path; import json; "
            "p=Path('/tmp/claude-login'); s=p/'status.json'; "
            "d=json.loads(s.read_text()) if s.exists() else {'status':'starting'}; "
            "d.update(credential=(p/'credential.txt').read_text()) if d['status']=='complete' else None; "
            "print(json.dumps(d))"])
        state = json.loads(payload or '{"status":"starting"}')
        if state["status"] == "complete":
            return _complete(core, pod, login_id, provider, state["credential"])
        if state["status"] in ("expired", "failed"):
            cancel(core, login_id)
            return {"status": state["status"]}
        if state["status"] == "waiting":
            from urllib.parse import urlsplit
            url = state.get("url", "")
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.hostname not in ("claude.com", "claude.ai") or parts.path != "/cai/oauth/authorize":
                raise ValueError("The Claude sign-in link was invalid. Start again.")
            return {"status": "waiting", "url": url}
        return {"status": "exchanging" if state["status"] == "exchanging" else "starting"}
    payload = execute(["/opt/venv/bin/python", "-c", "from pathlib import Path; "
        "p=Path('/tmp/codex-login/auth.json'); "
        "print(p.read_text() if Path('/tmp/login-done').exists() else '')"])
    if payload.strip():
        return _complete(core, pod, login_id, "codex", payload)
    logs = core.read_namespaced_pod_log(name, subscriptions.NAMESPACE, container="login", tail_lines=40)
    # Codex colours the code (ESC[94mABCD-EFGHI), which hides it from \b.
    logs = ANSI.sub("", logs)
    code = re.search(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b", logs)
    # Return only the device challenge, never raw CLI output.
    if code and "https://auth.openai.com/codex/device" in logs:
        return {"status": "waiting", "url": "https://auth.openai.com/codex/device", "code": code.group()}
    return {"status": "starting"}


def _complete(core, pod, login_id, provider, credential):
    # Two tabs can poll completion concurrently, or the response can be lost.
    for attempt in range(3):
        try:
            result = subscriptions.save(core, provider, pod.metadata.annotations["omnigent.dev/subscription-name"],
                credential, pod.metadata.annotations.get("omnigent.dev/subscription-id") or None,
                login_id=login_id)
            break
        except Exception as exc:
            if getattr(exc, "status", None) != 409 or attempt == 2:
                raise
    try:
        cancel(core, login_id)
    except Exception:
        # Saving succeeded; a cleanup failure must not turn it into a failed login.
        # The Pod's deadline bounds its life, and the next sign-in reaps it.
        pass
    return {"status": "complete", "subscriptions": result}


class HelperUnavailable(RuntimeError):
    """The helper didn't answer an exec; the next poll can try again."""


def _execute(core, name, command, input_text=None):
    from kubernetes import client
    from kubernetes.stream import stream
    # Exec mutates its ApiClient transport; don't reuse the REST client.
    exec_core = client.CoreV1Api(client.ApiClient(core.api_client.configuration))
    args = dict(command=command, container="login", stderr=False, stdin=input_text is not None,
                stdout=True, tty=False, _request_timeout=15)
    ws = None
    try:
        ws = stream(exec_core.connect_get_namespaced_pod_exec, name, subscriptions.NAMESPACE,
                    _preload_content=False, **args)
        if input_text is not None:
            ws.write_stdin(input_text + "\n")
        ws.run_forever(timeout=15)
        if ws.returncode != 0:
            # Not a ValueError: the browser keeps polling through a transient failure.
            raise HelperUnavailable()
        return ws.read_stdout()
    finally:
        if ws is not None:
            ws.close()
        exec_core.api_client.close()


def submit_code(core, login_id, code, execute=None):
    code = code.strip()
    if len(code) > 2048 or not CLAUDE_CODE.fullmatch(code):
        raise ValueError("Paste the whole code Claude shows after you authorize, including the part after #.")
    name = pod_name(login_id)
    pod = core.read_namespaced_pod(name, subscriptions.NAMESPACE)
    if (pod.metadata.labels or {}).get(LABEL) != "claude" or pod.status.phase != "Running":
        raise ValueError("This Claude sign-in is no longer running. Start again.")
    command = ["/opt/venv/bin/python", "-c", "import sys, os; from pathlib import Path; "
        "p=Path('/tmp/claude-login'); t=p/'.code.txt'; "
        "t.write_text(sys.stdin.readline().strip()); t.chmod(0o600); os.replace(t,p/'code.txt')"]
    (execute or (lambda command, code: _execute(core, name, command, code)))(command, code)
    return {"status": "exchanging"}
