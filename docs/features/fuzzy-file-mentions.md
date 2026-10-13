# Fuzzy file search with @

Type `@` and part of a file's name in a session's composer, and the menu
finds it anywhere in the workspace. You don't have to open each folder on
the way. It works like the `@` picker in Claude Code and Codex.

![Typing @auth lists auth.py from omnigent/server and omnigent/server/routes, a tests/e2e_ui/auth folder and more, with the matched letters in bold and each result's folder in grey](../images/fuzzy-file-mentions.webp)

## Using it

- **Type part of a name:** `@auth` lists every file and folder that matches,
  best first. Pick one with the arrow keys and Enter, or click it.
- **The letters don't have to be next to each other.** They only have to be
  in the right order, as in fzf: `@srvts` finds `src/server.ts`.
- **Search one folder:** `@src/au` searches everything under `src`, however
  deep.
- **Folders show up too.** Enter on a folder opens it, and Tab or **+ folder**
  attaches the whole folder.
- **Browsing still works.** A bare `@` lists the workspace's top level, and
  `@src/` lists what's in `src`, as before.

Matches in the file's own name rank above matches in the folders above it.
So do matches at the start of a word, after a `-`, `_` or `.`, or at a
capital letter, and letters typed in a row. Files rank a little above
folders, and shorter paths above longer ones. Typing a capital letter makes
the search match case.

## Good to know

- **Only the session's composer has it.** The `@` menu in the **New
  session** dialog lists the host's files and still browses one folder at a
  time. To search a sandbox before you've decided what to ask,
  [start the session without a message](start-without-message.md).
- **Ignored files aren't listed.** Inside a Git repository the menu offers
  what `git ls-files` lists: tracked files, and new files `.gitignore`
  doesn't exclude. A repository inside another one follows its own
  `.gitignore`. Outside a repository it skips hidden folders, `node_modules`
  and `.venv`.
- **The list loads when you first type `@`.** Until it arrives, the menu
  filters the current folder as before. It reloads when the agent finishes a
  turn, so new files show up.
- **Up to 50,000 files.** In a bigger workspace, files past that aren't
  offered.
- **If the list can't load,** for example while the runner is restarting,
  the menu browses folders as before.

## Turning it off

You can't.

## How it works

| Part | Patch | What it does |
| --- | --- | --- |
| Runner | `images/runner/patches/0006-mention-file-index.patch` | Adds a `file-index` route that lists every file path in the workspace, using `git ls-files` inside repositories |
| Server | `images/server/patches/0011-mention-file-index.patch` | Passes that route through to the runner, with the same access check as the other file reads |
| Web UI | `images/server/web-patches/0014-composer-fuzzy-mentions.patch` | Loads the list once, matches it as you type, and shows the results in the `@` menu |

The matching runs in the browser. Once it has 50 results, it skips any path
whose best possible score can't beat the 50th, before matching it. On the
Linux kernel's 96,000 files a keystroke takes under a millisecond on average
and about 3 ms at worst. The scoring follows fzf's. Upstream Omnigent's `@`
menu only filtered the folder you were in, so reaching a nested file meant
opening every folder on the way.
