# Making changes

Change files in Git, never on the server or in the cluster. Then deploy them
from your computer, or [from GitHub Actions](github-actions.md).

## The usual loop

```bash
mise run check               # validate your changes
mise run diff                # see what will change in the cluster
mise run deploy              # apply it
mise run status              # health, TLS, storage and failed sessions
mise run credential-status   # which credentials and repository variables are stored
```

Production is never deployed automatically.

## When you also need bootstrap

Some changes touch the VM or the platform, not just the application. For
those, run `mise run bootstrap` before you deploy:

- anything in `ansible/` or `kubernetes/platform/`
- the k3s, cert-manager or agent-sandbox versions in `versions.yaml`
- the environment's hostname, Kubernetes API host, ACME challenge or GitHub
  deploy settings

You don't have to remember this list. `mise run deploy` compares the cluster
with your checkout, and refuses to run until you've bootstrapped.

Other version bumps, such as the agent CLIs, only need a deploy.

## Common tasks

| Task | What to do |
| --- | --- |
| **Upgrade a version** | Change it in `versions.yaml`, then deploy. Bootstrap first for k3s, cert-manager or agent-sandbox. |
| **Change an image** (`images/`) | Push the change, then run the **Deploy** workflow. It builds, locks and deploys the images. See below to do it from your computer instead. |
| **Roll back** | Revert the commit and deploy again. |
| **Check the custom features after an upgrade** | See [keeping the patches up to date](features/README.md#keeping-the-patches-up-to-date). |

To change an image without the Deploy workflow:

1. Push your commit.
2. Run the **Publish immutable images** workflow on that commit.
3. With that commit checked out, run `mise run lock-images`.
4. Commit the new digests and deploy.

## More than one deployment

Every `mise` command works on the environment named by `ENV`, which defaults
to `production`. See [running more than one deployment](environments.md).
