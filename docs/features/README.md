# Custom features

This deployment runs upstream Omnigent `v0.15.0`, pinned as
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
| [Fuzzy file search with @](fuzzy-file-mentions.md) | Type `@auth` to find a file anywhere in the workspace, like in Claude Code | No |

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
- **Backported upstream fixes** come out when you upgrade past the release
  that has them:

  | Patch | Upstream fix | Delete from |
  | --- | --- | --- |
  | `images/runner/patches/0007-native-turn-idle-watchdog.patch` | [#8058](https://github.com/omnigent-ai/omnigent/pull/8058): a Claude or Codex turn that runs longer than `runner_agent_idle_seconds` is no longer stopped as idle | `v0.16.0` |
- **When no web patches are left**, the server image can stop rebuilding the
  web UI.

To check every feature after an upgrade, open an Omnigent session on the
deployment and ask the agent to run the `test-custom-features` skill
(`.claude/skills/test-custom-features/`). It checks that the patches apply,
tests the runner and the web UI, and lists the few checks you need to do
yourself.
