# Subscriptions in Settings

Admins can open **Settings → Subscriptions** to keep named Claude and
ChatGPT subscriptions, such as Personal and Work, and choose one active account
for each provider. The choice is shared across the deployment; Claude and Codex
can use different accounts. Switching does not require a deployment or the
`setup-claude` / `setup-codex` mise tasks.

## Adding and updating accounts

For Claude, enter a name and choose **Sign in with Claude**. Omnigent starts
Claude's official setup-token flow in an isolated server-side helper. Follow the
browser link, sign in and authorize access, then paste the one-time authorization
code into Settings and choose **Finish sign-in**. The helper exchanges the code
and stores the resulting subscription token without displaying it. Nothing needs
to be installed or run on your computer. The browser-code fallback follows
[Claude Code's official authentication flow](https://code.claude.com/docs/en/authentication).

For Codex, enter a name and choose **Sign in with ChatGPT**. Follow the link,
enter the one-time code and finish signing in. Enable device-code authorization
in ChatGPT's security settings if your account requires it. Omnigent stores the
login when the browser sign-in completes. Under
**Already have credentials? Import instead**, you can also import an `auth.json`
from an existing Codex ChatGPT login. Codex API-key logins are not accepted here.

The first saved account for a provider becomes active. Adding another leaves the
current choice intact. **Use** switches to a saved account. **Edit** renames an
account or replaces its credentials; leaving credentials blank preserves them.
Accounts for either provider can be signed in again from the editor to update the login.
You must switch away from an active account before removing it.
Credential import is optional; browser sign-in is the default for both providers.

The old shared login stays in use until you save your first account for that
provider. Once you manage a provider here, the Settings choice takes precedence
over its old mise setup task.

## Applying a switch to open sessions

Kubernetes syncs active credentials into runner Pods, usually within a minute
(and potentially longer depending on kubelet settings). Agent processes read the
selection when starting. Let the sync finish, then stop and resume an open
session to use the new subscription: choose **Stop session** in its menu, then
send another message to start it again. The composer’s interrupt button only
cancels a turn. A switch never interrupts a running turn.
New Pods read the current selection when they start.

Runners created before this feature was deployed need to be recreated once to
receive the subscription mount. Sleeping sessions receive it when they wake.

## Storage and permissions

The server stores the account list and credentials in the
`omnigent-subscriptions` Secret in `omnigent-sandboxes`. Listing and mutation
responses contain account names and IDs, never credentials. Only admins may
manage subscriptions, including device sign-in. Updates use Kubernetes
resource versions so concurrent edits fail with a reload message.

Only the active credentials and their selection IDs are projected into runner
Pods. Codex keeps writable auth files per subscription on the existing
`codex-home` PVC, preserving refreshed tokens when switching away and back.
Replacing a credential creates a new revision so the next launch replaces the
old writable login. The PVC also retains previously used logins after an account
is removed; deleting a saved account does not revoke the provider's tokens.
As with the existing shared Codex home, runners can access these writable logins.
This feature assumes a trusted operator or team, not isolation between users.

Browser sign-in uses temporary runner-image Pods with no service-account token,
no mounted credentials and a 15-minute deadline. Each helper requests 64 MiB of
ephemeral storage and is limited to 128 MiB. Only the sign-in URL and, for Codex,
the device code are returned to the browser. Claude terminal output stays inside
its helper; its subscription token is collected server-side and never appears in Pod logs. The
one-time code submitted by the user travels through exec stdin rather than command
arguments. Invalid or failed sign-ins can be restarted from the dialog. Completed
and cancelled helpers are deleted; expired helpers are cleaned up at the next
sign-in attempt. At most three helpers may run at once on the single server replica. Completion is idempotent: a
retried poll cannot create a duplicate account, and a failed Pod cleanup does not
turn a saved login into an error. Transient polling errors are retried in the UI.
The server needs `pods/exec` permission to collect a completed login, plus its existing Pod and Secret permissions.

Build and deploy both server and runner images and apply the platform RBAC update
for initial installation. Subsequent account changes happen entirely at runtime.

## Verification

Add Personal and Work for one provider, choose **Use** on Work, and confirm its
Active badge moves. Wait for credentials to sync, stop and resume a session, and
check that the provider's usage corresponds to Work. Switch back to Personal and
repeat. Rename Work without providing credentials and confirm it still works.
Try removing the active account: its remove button is disabled. Switch away and
remove it. On Codex, also complete or cancel a device sign-in and confirm the
helper Pod is deleted. A non-admin should see neither the navigation item nor
subscription data via the API.
