# Operations and recovery contract

All normal changes start in Git. Edit `versions.yaml`, the selected environment
TOML file, Kubernetes resources, image inputs, or Ansible, review the diff, then
run:

```bash
make check ENV=production
make diff ENV=production
make deploy ENV=production
make status ENV=production
```

Do not edit generated files under `.generated`, resources directly in the
cluster, or files on the server. ConfigMap hashes trigger server rollout.
Secret helpers update the Secret and the next deployment derives an opaque
checksum annotation without printing the values.

Image changes are released by manually running the `Publish immutable images`
GitHub Actions workflow. Bump `image_release`, publish, run `make lock-images`,
review and commit the resolved digests, then deploy. Rollback means reverting
the Git commit containing the manifest/configuration/image digest and running
`make deploy` again.

There is no backup or restore service. Losing the VM or disk loses PostgreSQL,
artifacts, account state, OAuth state, and integration state. Recovery means a
clean VM, a fresh deployment, and reauthentication. Database-incompatible
upgrades must be tested against a disposable installation and explicitly
approved because they are not reversible under this policy.

`make status` reports Kubernetes readiness, TLS, PVCs, resource usage, warning
events, disk usage, failed Jobs, and observed OOM kills. Use the netcup console
or an external HTTPS monitor to detect total-host failure, because software on
the failed host cannot report its own outage.

