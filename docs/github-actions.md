# Deploying from GitHub Actions

Instead of deploying from your computer, you can deploy from the **Deploy**
workflow in your fork's Actions tab.

## What the workflow does

After you approve the run once, the workflow:

1. builds both images
2. commits their digests to the deployment's environment file on `main`
3. deploys that commit

It only runs when:

- you start it by hand from the Actions tab
- it runs from `main`
- it runs in the GitHub Environment named after the deployment, such as
  `production`

## How it logs in

The workflow stores no credentials:

- It joins your tailnet to reach the Kubernetes API.
- It logs in to Kubernetes with the job's short-lived GitHub OIDC token.
- Bootstrap configures the Kubernetes API to accept that token only from
  your repository's `deploy_github_environment`, on `main`.

That login can only manage the application in the two Omnigent namespaces.
It can't change the platform or touch the VM. The job can also push images
to GHCR and commit to `main`.

> **Approving a run is a big deal.** An approved run can read and change
> everything in the two Omnigent namespaces, including the database and all
> secrets. It also decides which images production runs. See the
> [threat model](threat-model.md#deploying-from-github-actions).

## Set it up

### 1. Bootstrap with the GitHub settings

In `environments/production.toml`, set `deploy_github_repository_id` and
`kubernetes_api_host`. Then run:

```bash
mise run bootstrap
git add environments/production.kubernetes-ca.crt
git commit -m "Record the cluster CA"
```

### 2. Set up Tailscale for CI

In the Tailscale admin console:

1. **Add the tags.** Add `tag:omnigent` and `tag:omnigent-ci` to
   `tagOwners`.
2. **Tag the VM** `tag:omnigent`. Keep any tags it already has.
3. **Limit CI's access.** Allow `tag:omnigent-ci` to reach `tag:omnigent` on
   `tcp:6443`, and nothing else.
4. **Add a trust credential.** Under **Trust credentials**, add an OpenID
   Connect credential:

   | Field | Value |
   | --- | --- |
   | Issuer | `https://token.actions.githubusercontent.com` |
   | Scope | `auth_keys` (write) |
   | Tag | `tag:omnigent-ci` |
   | Subject | Your repository's `production` Environment; see below |

   The subject depends on when the repository was created:

   - after July 15, 2026:
     `repo:OWNER@OWNER_ID/REPO@REPO_ID:environment:production`
   - before that: `repo:OWNER/REPO:environment:production`

### 3. Create the GitHub Environment

In your fork, go to **Settings → Environments** and create `production`:

- Add yourself as a **required reviewer**.
- Limit deployment branches to `main`.
- Add two Environment secrets from step 2: `TS_OAUTH_CLIENT_ID` and
  `TS_AUDIENCE`. They aren't sensitive, but secrets are hidden in public run
  logs.

## Deploying another environment

To deploy another environment from the same workflow, such as
`production-alternative` from [more than one deployment](environments.md):

1. Set its `deploy_github_environment` to its own name.
2. Run its bootstrap.
3. Repeat steps 2 and 3 above, using its name instead of `production`.
4. Add it to the `environment` options in `.github/workflows/deploy.yml`.
5. Tag its VM `tag:omnigent` too.

## What still runs from your computer

- `mise run bootstrap`
- the credential setup tasks: `setup-codex`, `setup-claude`,
  `setup-git-token`, `setup-github-app` and `setup-cloudflare-token`
