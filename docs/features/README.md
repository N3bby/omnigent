# Custom features

This deployment runs upstream Omnigent `v0.18.0`, pinned as
`omnigent_commit` in `versions.yaml`, with a few extras on top. Each page
explains what the feature does, what you'll notice, and how to turn it off
where you can.

## In the chat

What you see and choose while talking to an agent.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Usage limits in the UI](usage-limits.md) | Your Claude and Codex usage, right in the composer | No |
| [Fast mode from the chat](fast-mode.md) | Send `/fast` to switch Claude's or Codex's fast mode, and see when it's on | Fast mode is off until you send `/fast` |
| [Model picker straight away](model-picker.md) | Choose a model before your first session has run | No |
| [Fuzzy file search with @](fuzzy-file-mentions.md) | Type `@auth` to find a file anywhere in the workspace and write it into your message, like in Claude Code | No |
| [The environment menu](environment-menu.md) | See whether the sandbox is running, starting or stopped, copy its SSH command, restart the agent, or stop the sandbox and keep its files | Nothing to turn off |
| [The startup card](startup-progress.md) | Each step of a sandbox's start as it happens, why it failed in plain words, and the whole startup log | Nothing to turn off |
| [Start without a message](start-without-message.md) | Start a session from the New session screen with an empty composer, so its sandbox and @ file search are ready before you ask anything | Nothing to turn off |
| [Your first message survives closing the app](first-message-while-starting.md) | Send a new session's first message and close the app while the sandbox starts: the agent still gets it | Nothing to turn off |

## Subscriptions

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Subscriptions in Settings](subscriptions.md) | Sign in to several Claude and ChatGPT accounts from the browser, switch between them, and see which one a session uses | The mise-stored login stays until you add an account |

## The session's runner

What agents can do in their session, and what happens to it when you leave.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Root and containers](root-and-containers.md) | Agents can `apt-get install`, and use Docker, Compose and Testcontainers | No |
| [Runtimes from mise](mise-runtimes.md) | `node`, `python` and others follow a project's `.mise.toml` or `.tool-versions` | No |
| [No permission prompts](no-permission-prompts.md) | Agents work without waiting for approval | Yes, one setting per agent |
| [Idle sessions stop](idle-sessions.md) | Idle sessions free their CPU and memory, and keep their files | You can change the timing |
| [Agents know where they're running](runner-instructions.md) | Agents know what the session can do, link files you can open, and give tailnet addresses | You can change the text |
| [MCP servers in Settings](mcp-servers.md) | Give sessions' Claude Code and Codex MCP servers, such as Figma, with their keys: for every session or per repository | Nothing to turn off until you add a server |

## Repositories

Getting your repositories into a session, with what they need to build.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [GitHub repository picker](github-repo-picker.md) | Choose repositories from a list | Optional setup step |
| [Repository variables](repo-env.md) | A repository's own environment variables, such as an npm token, in its sessions | Optional setup step |
| [A repository's own Git token](repo-git-token.md) | Pick, clone and push a repository the GitHub App can't reach, with its own token | Optional setup step |
| [A repository's own agent instructions](repo-instructions.md) | Your own instructions for a repository's agents, kept out of its `CLAUDE.md` | Optional setup step |
| [A repository's setup script](repo-setup-script.md) | A script that sets up each new session on a repository before the agent starts | Optional setup step |
| [A project's repositories](project-repositories.md) | A new session in a project picks that project's repositories, not the last ones you used | A project without repositories keeps the old behaviour |

## Reaching a session from your devices

Working with a session from your own computer or phone, outside the web UI.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Sessions on your tailnet](tailscale.md) | SSH into a session, or open its dev servers, from your own devices | Optional setup step |
| [Open in JetBrains Gateway](jetbrains-gateway.md) | Open the session's repository in IntelliJ | Needs Tailscale |

## Look and feel of the web UI

Changes to how the web UI looks and behaves.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Codex theme](codex-theme.md) | A colour theme that looks like the Codex app, beyond just the colours | It's off until you pick it in Settings → Appearance |
| [The sandbox picker under the composer](sandbox-picker.md) | On the New session screen, where the session runs is a named chip next to the repository | No |
| [Swipe for a session's menu](session-swipe-menu.md) | On a phone, swipe a session left for its menu; holding it down only moves it | No |
| [No Share button](no-share-button.md) | Sessions can't be shared from the web UI | No |
| [No pin or archive buttons in the sidebar](no-sidebar-quick-actions.md) | Hovering a session no longer shows buttons you can hit by accident; Pin and Archive are in its ⋯ menu | No |

## Words used on these pages

- **Session**: one conversation with an agent in Omnigent.
- **Runner** or **Pod**: the container a session's agent runs in. Each
  session has its own.
- **Composer**: the box at the bottom of a session where you type messages.
- **Patch**: a change to upstream Omnigent's code, applied when the images
  are built. Most features are one or more patches in `images/*/patches/`
  and `images/server/web-patches/`.

## Keeping the patches up to date

The patches are applied when the images are built. If a patch no longer
applies to the pinned `omnigent_commit`, both the image build and
`mise run test` fail.

- **When upstream ships the same behaviour**, delete the patch.
- **Upstream bugs this deployment fixes** come out once upstream ships a fix:

  | Patch | Upstream bug | Delete when |
  | --- | --- | --- |
  | `images/server/web-patches/0024-admin-settings-nav.patch` | Since [#8634](https://github.com/omnigent-ai/omnigent/pull/8634) (`v0.17.0`), Settings has no Admin group (Members, Policies, Sharing, Subscriptions, Repository variables) after a page load | upstream's `web/src/hooks/useIsAdmin.ts` no longer seeds `initialData` |
  | `images/server/patches/0018-binary-preview-gzip.patch` | On Starlette 1.8.0, which the server image resolves in place of the locked 1.3.1, the file viewer answers "Error loading file: 500" for images and PDFs, and downloads fail too, whenever the browser accepts gzip ([#10481](https://github.com/omnigent-ai/omnigent/issues/10481)) | upstream merges [#10585](https://github.com/omnigent-ai/omnigent/pull/10585), so `omnigent/server/routes/_gzip_route.py` reads `skip_gzip` on `http.response.start` |
  | `images/server/web-patches/0026-workspace-rail-shift.patch` | Since v0.16 the closed workspace rail sits past the window's right edge and, because React 18 drops its boolean `inert`, stays in the Tab order; focusing it shifts the whole app, sidebar included, 12px to the left | upstream's `web/src/shell/WorkspacePanel.tsx` sets `inert` on the rail's node, or upstream moves to React 19 |
  | `images/server/web-patches/0027-shortcut-key-width.patch` | Keyboard shortcut hints overlap on Windows and Linux ("CtrlAlt N"): each key cap is a fixed 16px square, and its `text-10` class isn't a Tailwind utility, so it takes the row's text size | upstream's `web/src/components/ui/kbd.tsx` no longer uses `size-4` and `text-10` |
- **When no web patches are left**, the server image can stop rebuilding the
  web UI.

To check every feature after an upgrade, open an Omnigent session on the
deployment and ask the agent to run the `test-custom-features` skill
(`.claude/skills/test-custom-features/`). It checks that the patches apply,
tests the runner and the web UI, and lists the few checks you need to do
yourself.
