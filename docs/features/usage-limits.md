# Usage limits in the UI

Claude and Codex subscriptions have a 5-hour limit and a weekly limit. The
composer shows how much of each you've used, next to the context ring. You
won't hit a limit halfway through a task by surprise.

![Illustration of the usage limits in the composer](../images/usage-limits.svg)

## Using it

- Click or tap the numbers to see bars and reset times.
- It works in the browser, and in the desktop and mobile apps.

## Good to know

- **The numbers appear once the agent first reports them.** For Claude,
  that's after its first reply. For Codex, it's when the first turn starts.
- **The numbers belong to the login**, so every session using the same
  account shows the same numbers.

## Turning it off

You can't.

## How it works

Three patches work together:

| Part | Patch | What it does |
| --- | --- | --- |
| Runner | `images/runner/patches/0001-rate-limits.patch` | Passes on the usage numbers that the Claude and Codex CLIs report. |
| Server | `images/server/patches/0002-rate-limits.patch` | Keeps the latest numbers for each session and sends them to the UI. |
| Web UI | `images/server/web-patches/0001-composer-rate-limits.patch` | Adds the `ComposerRateLimits` display. |

The upstream server image comes with a prebuilt web UI. To apply the web
patch, the server image build rebuilds the UI from source.
