# A repository's exit node

Sessions on a repository can send their internet traffic through one of your
tailnet's exit nodes. Take a GitLab that only accepts a VPN's addresses: give
the repository the exit node that's connected to that VPN, and its sessions
clone, fetch and push through it.

## Set it up

### 1. Put sessions on your tailnet

Follow [sessions on your tailnet](tailscale.md), including
`mise run bootstrap`, which gives runner Pods the TUN device the exit node
needs.

### 2. Let sessions use the exit node

The machine has to offer to be an exit node, and be approved as one in the
[admin console](https://login.tailscale.com/admin/machines). Then let
sessions use it, in your [tailnet policy](https://login.tailscale.com/admin/acls):

```json
"grants": [
  {"src": ["tag:omnigent-runner"], "dst": ["autogroup:internet"], "ip": ["*"]}
]
```

To allow only some exit nodes, give them a tag and add
`"via": ["tag:cronos-exit"]`. `via` takes tags, not machine names.

To check, run `tailscale exit-node list` in a session. The exit node should be
in the list.

### 3. Give the repository its exit node

In **Settings → Repository variables**, add or edit the repository, switch on
**Exit node**, and type the exit node's machine name, such as `cronos-vpn`.
Its MagicDNS name or Tailscale IP works too. Then **Save**.

![The repository's editor in Settings, with the Exit node switch on and cronos-exit typed in](../images/repo-env-exit-node.webp)

## What you'll see

- **In Settings**, the repository's card lists `Exit node cronos-vpn`.
- **In the new-session form**, the repository's badge has an exit node
  entry: "Internet traffic and the clone go through cronos-vpn on your
  tailnet."
- **On the startup card**, the clone's log says
  `cloning through exit node cronos-vpn`, and the Tailscale line ends with
  `through exit node cronos-vpn`.
- **In the session**, `tailscale status` marks `cronos-vpn` as the exit node,
  and `curl https://ifconfig.me` shows the exit node's address.

## Good to know

- **All the session's internet traffic goes through it:** the agent's own
  connection to Claude or OpenAI, package downloads and Docker pulls too.
  Only the cluster's own addresses don't, such as the Omnigent server and
  the cluster's DNS.
- **If the exit node is offline, the session has no internet,** so the agent
  can't reach Claude or OpenAI either. A new session on the repository fails
  at its clone, and says the exit node is offline.
- **Names still resolve through the cluster's DNS.** A Git server whose name
  only resolves inside the VPN can be cloned, but the session can't resolve
  the name afterwards. Use its public name or its address.
- **A session on several repositories** uses an exit node only if they name
  the same one. When they name different ones, it uses none, and the startup
  card says so.
- **Changes reach new sessions.** A running session gets them after it has
  been [idle](idle-sessions.md) and wakes up.
- **The clone takes a few seconds longer.** It joins your tailnet as a
  temporary machine, `omnigent-clone`, which logs out when the clone is done.
- **Without the TUN device**, such as before `mise run bootstrap`, the clone
  still goes through the exit node. In the session, only the proxy on
  `localhost:1055` uses it.
- **An exit node isn't a security boundary.** The agent is root in its Pod:
  it can switch the session to any exit node your policy lets sessions use,
  or to none. Anyone signed in can see a repository's exit node in the
  new-session form, and only admins can set it. See the
  [threat model](../threat-model.md#runners-on-your-tailnet).

## Turning it off

Edit the repository in Settings → Repository variables and switch off
**Exit node**. A repository without one works as before.

## How it works

- **Where it's kept:** the `tailscale-exit-node` key of the repository's
  [Secret](repo-env.md#how-it-works) (`EXIT_NODE_KEY` in
  `images/server/omnigent_repo_env.py`). It isn't a variable name, so the
  agent wrappers don't export it. It isn't secret either: the Settings page
  reads it back, and `GET /v1/repo-env/names` returns it for the new-session
  form. `check_exit_node` only allows a machine's name or address.
- **The page:** `images/server/web-patches/0036-repo-exit-node.patch` adds the
  switch to Settings → Repository variables, and the entry to the
  repository's badge. `PUT /v1/repo-env` takes `exit_node`; a page that
  doesn't send it keeps the one the repository has.
- **The clone:** `clone_command` in `images/server/omnigent_repo_env.py`
  wraps the repository's clone in a shell function. When the mounted
  `tailscale-exit-node` file has a name, the function runs the clone through
  `omnigent-via-exit-node` (`images/runner/tailscale/via-exit-node.sh`):
  - The `workspace-prep` init container has no TUN device and no
    capabilities, and the Pod's own node only joins once the host container
    starts. So it starts a `tailscaled` with userspace networking and its
    state in memory, joins as an ephemeral `omnigent-clone`, sets the exit
    node, and waits until it's online.
  - Git gets the node's SOCKS5 proxy as `http.proxy`, through
    `GIT_CONFIG_COUNT`, and an `ssh` first on its `PATH` that connects with
    `ProxyCommand=tailscale nc`. That covers a repository's
    [SSH key](custom-repository.md#how-it-works) too, which calls `ssh` by
    name. Only Git gets the proxy: the GitHub App's credential helper talks
    to the Omnigent server, which isn't on the tailnet.
  - The node logs out when the clone ends.
- **The session:** `images/runner/tailscale/tailscale-service.sh` reads
  `/run/omnigent/repo-env/*/tailscale-exit-node` after joining, and runs
  `tailscale set --exit-node=<name> --exit-node-allow-lan-access`. Its rules
  for the cluster's ranges keep the Omnigent server and the cluster's DNS off
  the exit node; see [how Tailscale works in a session](tailscale.md#how-it-works).
  It reports the exit node, or why it couldn't use it, on the startup card's
  Tailscale line.
