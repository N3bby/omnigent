# Open in JetBrains Gateway

Open a session's repository in IntelliJ on your computer, through
[JetBrains Gateway](https://www.jetbrains.com/remote-development/gateway/).
The IntelliJ backend is already installed in every session.

## What you'll see

On desktop, the composer shows **Open in Gateway** next to the Tailscale name.
It appears when the session has a Tailscale name and a project directory. The
gray Gateway icon and its label are one control. It's hidden on mobile.

## Before you start

- Set up [sessions on your tailnet](tailscale.md).
- Install JetBrains Gateway on your computer.
- Connect your computer to the same tailnet.

## Using it

1. **Wake the session** if it has stopped, by sending it a message.
2. **Click Open in Gateway**, and allow your browser to open Gateway.
   In the Omnigent desktop app, choose **Open** when it asks to open the
   `jetbrains-gateway` link. Select **Always allow** to skip the question
   next time.
3. Gateway connects over Tailscale SSH as `root`, and opens the session's
   repository, such as `/home/omnigent/workspace/my-project`.

The first connection can still ask for SSH authentication, or whether you
trust the project.

Gateway starts the backend for the repository when it connects, and
downloads the matching local client on Linux or macOS.

## Good to know

- **The session can stop while you're in the IDE.** IDE activity doesn't
  count as agent activity. A session stops after 1 hour without agent
  activity, even while you're using the IDE. See
  [idle sessions](idle-sessions.md).
- **IDE settings and caches are lost** when the Pod is recreated. They live
  under `/root`, which isn't kept. Your repository files under
  `/home/omnigent` are kept.
- **Closing the local client can leave the backend running** until it stops
  or the Pod shuts down.
- **The first session on a VM starts slower**, because the backend makes the
  runner image about 4.5 GiB larger when unpacked. The VM downloads the image
  before its first session starts, and reuses it for later sessions.
- **Installing the backend costs no CPU or RAM.** It doesn't start an IDE
  process until Gateway connects.

## Memory use

Estimates for planning, not measurements of your project. They exclude
agents, builds and the application itself.

| Project | Backend RAM |
| --- | --- |
| Typical | about 2–4 GiB |
| Large, with a larger heap | about 4–8 GiB or more |

The bundled default maximum Java heap is 2 GiB. The process can use more than
that, because of
[native allocations and other JVM overhead](https://intellij-support.jetbrains.com/hc/en-us/articles/360018776919-IntelliJ-IDE-uses-more-memory-than-maximum-heap-size-Xmx).

Session CPU and memory limits are deliberately above the VM's capacity:
requests guide scheduling rather than capping usage. Watch actual usage
before you change reservations or `runner_max_concurrency`.

## Turning it off

It only shows when the session is on your tailnet. The backend is always in
the runner image.

## How it works

- **Runner image:** downloads the IntelliJ IDEA Ultimate archive pinned in
  `versions.yaml` (`jetbrains_idea_*`), checks its SHA-256 digest, and
  unpacks it to `jetbrains_idea_path`. The build fails if the backend's
  `product-info.json` isn't the pinned build.
- **Web UI** (`images/server/web-patches/0003-composer-gateway-link.patch`):
  adds `ComposerGatewayLink` next to the Tailscale name.
  - It builds the `jetbrains-gateway://connect` link from the session's full
    Tailscale name, its workspace, and the backend path. The server image
    build passes the path in as `VITE_OMNIGENT_GATEWAY_IDEA_PATH`.
  - In the desktop app the link opens as a new window, because the app blocks
    same-window links to other apps.
- **Why `deploy=false`:** the link points Gateway at the installed backend
  with `deploy=false`. Gateway's automatic-deployment links need an `ssh`
  connection ID saved on your computer, which can't be shared between users,
  or between Linux and macOS.
