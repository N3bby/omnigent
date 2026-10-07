# Custom features

This deployment runs upstream Omnigent `v0.15.0`, pinned as
`omnigent_commit` in `versions.yaml`, with a few extras on top. Each page
explains what the feature does, what you'll notice, and how to turn it off
where you can.

| Feature | What you get | Can you turn it off? |
| --- | --- | --- |
| [Usage limits in the UI](usage-limits.md) | Your Claude and Codex usage, right in the composer | No |
| [Model picker straight away](model-picker.md) | Choose a model before your first session has run | No |
| [Root and containers](root-and-containers.md) | Agents can `apt-get install`, and use Docker, Compose and Testcontainers | No |
| [Runtimes from mise](mise-runtimes.md) | `node`, `python` and others follow a project's `.mise.toml` or `.tool-versions` | No |
| [No permission prompts](no-permission-prompts.md) | Agents work without waiting for approval | Yes, one setting per agent |
| [GitHub repository picker](github-repo-picker.md) | Choose repositories from a list | Optional setup step |
| [Idle sessions stop](idle-sessions.md) | Idle sessions free their CPU and memory, and keep their files | You can change the timing |
| [Sessions on your tailnet](tailscale.md) | SSH into a session, or open its dev servers, from your own devices | Optional setup step |
| [Open in JetBrains Gateway](jetbrains-gateway.md) | Open the session's repository in IntelliJ | Needs Tailscale |
| [No Share button](no-share-button.md) | Sessions can't be shared from the web UI | No |
| [Agents know where they're running](runner-instructions.md) | Agents know what the session can do, link files you can open, and give tailnet addresses | You can change the text |

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
- **When no web patches are left**, the server image can stop rebuilding the
  web UI.

To check every feature after an upgrade, open an Omnigent session on the
deployment and ask the agent to run the `test-custom-features` skill
(`.claude/skills/test-custom-features/`). It checks that the patches apply,
tests the runner and the web UI, and lists the few checks you need to do
yourself.
