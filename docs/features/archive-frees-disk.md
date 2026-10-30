# Archiving a session frees its disk

Archiving a session keeps its conversation and deletes its sandbox, with the
home directory's files. Upstream keeps the sandbox until you delete the
session, so every archived session went on using the VM's disk.

## What you'll notice

- **Archive** in a session's ⋯ menu works as before: the session leaves the
  sidebar, and **Undo** brings it back untouched.
- **Once the Undo pill is gone**, the session's Pod stops and its sandbox and
  home volume are deleted. `mise run status` no longer lists its Sandbox or
  its `home-omnigent-managed-…` volume.
- **Unarchiving** brings the conversation back, and the session reads as
  asleep. Its next message starts a fresh sandbox, with the usual startup
  card, and the chat says:

  > The sandbox backing this session no longer exists. A fresh sandbox is
  > being created. Local files that were not committed and pushed are gone.
  > If the session started from a repository, that repository has been
  > cloned again.

- **Sessions archived before this feature** lose their sandbox the next
  time the server starts, such as on the next deploy.

Push your work before you archive a session. Uncommitted changes, other
files in the home directory and installed tools are gone once the Undo pill
is.

## Can you turn it off?

No. To keep a session's files, leave it unarchived; an idle session still
[stops and keeps its files](idle-sessions.md).

## How it works

- **The archive teardown deletes the sandbox.** Upstream already stops an
  archived session's runner once the Undo window has passed, after checking
  that the session is still archived. The server patch
  `images/server/patches/0026-archive-deletes-sandbox.patch` then deletes
  the session's `Sandbox` and token Secret, the way deleting the session
  does, and Kubernetes deletes the home volume with the `Sandbox`.
- **The host record stays**, still naming the deleted sandbox. That's what
  makes an unarchived session read as asleep, not offline: its next message
  tries to wake the sandbox, finds it gone, and starts a fresh one through
  upstream's own path for a lost sandbox, which clones the session's
  repository again.
- **Shared hosts are left alone.** A sandbox another unarchived session is
  bound to, such as one a terminal was moved to, isn't deleted.
- **On every start**, the server goes through the archived sessions and
  deletes any sandbox they still have. Deleting one that's already gone
  succeeds, so this is cheap.
- **Other hosts aren't touched**: your own computers, the
  [Hermes host](hermes-host.md), and sandbox providers that don't keep a
  stopped sandbox.
