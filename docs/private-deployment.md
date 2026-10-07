# Private deployment on your tailnet

By default, the VM must be reachable from the internet on port 80. This page
shows how to run a deployment that's only reachable over
[Tailscale](https://tailscale.com), with nothing open to the internet.

## Why port 80 is needed by default

To issue a certificate, Let's Encrypt checks that you control the hostname.
By default it does that by fetching a file from the VM over the internet.
This is the **HTTP-01** challenge.

A private VM uses the **DNS-01** challenge instead:

1. cert-manager creates a temporary `_acme-challenge` TXT record for the
   hostname, through the Cloudflare API.
2. Let's Encrypt looks up that record in public DNS. It never connects to the
   VM.
3. cert-manager deletes the record and stores the certificate.

cert-manager repeats this about 30 days before the certificate expires.

You get the same publicly trusted certificate. The VM only needs outbound
HTTPS and DNS.

## What you need

- **Your DNS zone on Cloudflare.** It's the only DNS provider this setup
  supports.
- **A Cloudflare API token.** In the Cloudflare dashboard, go to
  **My Profile → API Tokens** and create a custom token:
  - permissions: **Zone / DNS / Edit** and **Zone / Zone / Read**
  - zone resources: only your zone

  Anyone with this token can change every DNS record in that zone. See the
  [threat model](threat-model.md#the-cloudflare-dns-token).
- **Tailscale** on the VM, and on every device that uses Omnigent.

## Add a new private deployment

Follow [adding a deployment](environments.md#add-a-deployment), with these
three changes.

**Before you start: add the DNS record.** In Cloudflare, add an `A` record
for the hostname, such as `omni-internal.example.org`:

- point it at the VM's Tailscale IP (run `tailscale ip -4` on the VM)
- set its proxy status to **DNS only**, because Cloudflare's proxy can't
  reach a Tailscale address

Anyone can look up the record, but only devices on your tailnet can connect
to the address.

**In the environment file, also set:**

```toml
hostname = "omni-internal.example.org"
acme_challenge = "cloudflare-dns-01"
```

**Between bootstrap and deploy, store the Cloudflare token:**

```bash
ENV=production-alternative mise run setup-cloudflare-token
```

What to expect:

- Bootstrap checks that the hostname points at one of the VM's addresses.
- `setup-cloudflare-token` checks the token with Cloudflare before storing it.
- The deploy waits for the certificate. That usually takes a minute or two.

## Make an existing deployment private

1. Add its DNS record, as above.
2. In its environment file, set the new `hostname` and
   `acme_challenge = "cloudflare-dns-01"`.
3. Run, with its `ENV`:

   ```bash
   mise run bootstrap
   mise run setup-cloudflare-token
   mise run deploy
   ```

4. If GitHub Actions deploys it, also allow `tag:omnigent-ci` to reach
   `tag:omnigent` on `tcp:443`. The deploy checks the HTTPS endpoint.

## Troubleshooting

**The name doesn't resolve on some device.**
Some routers and DNS filters, such as Pi-hole or a Fritz!Box, drop public
answers that point to private or `100.x` addresses. This is called "DNS
rebinding protection". Either allow your domain there, or, in Tailscale's DNS
settings, add a public global nameserver such as `1.1.1.1` and have it
override the devices' local DNS.

**The certificate isn't issued.** Add `ENV=<name>` to these commands for a
deployment other than `production`:

- `mise run status` shows cert-manager's challenge and why it's stuck.
- `mise run credential-status` shows whether the token is stored.
- Storing the token again makes cert-manager retry straight away.

**You can't connect, and your Tailscale rules restrict traffic.**
Allow your devices to reach the VM on `tcp:443`, and on `tcp:80` for the
redirect to HTTPS.

## Good to know

- **The hostname is public.** Every publicly trusted certificate is listed in
  public Certificate Transparency logs, so anyone can find the name. The
  service itself stays unreachable from outside your tailnet.
- **A VM with a public address still serves on it.** Traefik listens on ports
  80 and 443 on every network interface, and this setup's firewall only
  blocks the Kubernetes API. If the VM has a public address, block those
  ports in your hosting provider's firewall.
