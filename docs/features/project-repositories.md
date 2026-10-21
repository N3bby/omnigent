# A project's repositories

Give a project the repositories its sessions work on. When you start a new
session in that project, those repositories are already picked. Without this,
the picker starts with the last repositories you launched any session with,
whichever project it was in.

## Set it up

1. In the sidebar, open the project's **⋯** menu (on a phone, hold the
   project down) and choose **Project settings**.
2. Set **Host** to the sandbox, or leave it on **No default**.
3. Under **Repositories**, pick one or more from **Choose a repository…**, or
   paste a repository's URL and choose **Add**. Each one gets a branch menu;
   leave it on the default branch, or pick another.
4. Choose **Save**.

Start a new session from the project's pencil, or choose the project in the
new-session form: the repositories are picked, with their branches.

## Good to know

- **It's a starting point.** You can still change the repositories for one
  session before you send the first message. That doesn't change the project.
- **A project without repositories works as before.** Its new sessions start
  with the last repositories you launched with.
- **A half-written session keeps your choice.** If you change the
  repositories, leave the form and come back, your choice stays rather than
  the project's.
- **Only the sandbox clones them.** Setting the project's host to one of your
  own machines removes its repositories when you save; that host uses the
  working directory instead. With the sandbox there's no working directory,
  as in the new-session form.
- **The list is the same as the new-session picker's Repositories tab**:
  the repositories the [GitHub App](github-repo-picker.md) is installed on,
  and, under **Configured in Settings**, every repository in Settings →
  Repository variables, on any host, such as one you
  [added with its own token or SSH key](custom-repository.md). A pasted URL
  works too, but a token or key can only be given in the new-session form's
  Custom tab or in Settings.
- **They're yours.** Projects belong to the user who made them, so other users
  don't see them.

## Turning it off

Remove the project's repositories in **Project settings** and save.

## How it works

- Upstream Omnigent stores a project's settings as a JSON object the server
  keeps as it is, and the web UI decides what's in it. The repositories are
  stored there as `sandbox_repos`, a list of `{url, branch}`, so there's no
  server change.
- `images/server/web-patches/0019-project-repositories.patch`:
  - Adds the **Repositories** field to the project settings dialog. It reuses
    the new-session form's repository and branch pickers, made modal so their
    lists can scroll inside the dialog.
  - In the new-session form, once the project's settings have loaded, replaces
    the remembered last repositories with the project's, unless you already
    changed them. It only replaces them again when you switch projects or the
    project's settings change.
- `images/server/web-patches/0031-repo-picker-tabs.patch` adds the
  repositories configured in Settings to the dialog's picker, as in the
  new-session form.
