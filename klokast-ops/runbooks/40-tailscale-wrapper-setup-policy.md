# Controller Tailscale credential setup

Each `<box>-ops` controller stores its own OAuth credentials root-only under
`/etc/klokast/`. The controller Ansible role installs the root-owned wrappers
and their privilege rules. Run Platform operations as `smith` there.
See [Tailscale automation](../tailscale/AGENTS.md) and
[Architecture](../../doc/architecture.md#overlay-management-plane).

## Credentials

Prepare two distinct scoped OAuth clients for each controller through the
human administrator. Do not reuse the active controller's clients on the
standby. Install independent credentials only after promotion:

- `tailscale-policy.env`: policy and one-use enrollment operations, with the
  managed tags needed by the installed wrappers.
- `tailscale-devices.env`: device lifecycle operations. Keep this credential
  separate from the policy and enrollment credential.

Each local input file has these fields:

```text
TAILNET_ID="your-tailnet-id"
TS_OAUTH_CLIENT_ID="your-oauth-client-id"
TS_OAUTH_CLIENT_SECRET="your-oauth-client-secret"
```

Keep the files private and outside Git. Use the existing human secret-storage
path; do not send credentials through chat or command arguments.

## Install or rotate

From the operator MacBook, send the private input files to the explicit
active controller after promotion:

```sh
klokast-dev/bin/install-tailscale-oauth \
  --controller boxb-ops \
  --policy-env /path/to/private/tailscale-policy.env \
  --devices-env /path/to/private/tailscale-devices.env
```

The helper sends that controller's own file contents through stdin and installs
mode `0600`, root-owned files on the controller. It checks controller and AI runner
enrollment configuration and device listing without printing credentials.

For an additional purpose, run the installed wrapper's `--check-config` on the
selected controller with the exact intended hostname and tags. For example:

```sh
sudo -n /usr/local/sbin/ts-authkey-ops \
  --check-config --hostname boxb-ops --tags tag:ops
```

A successful check prints `ok`. A tag-scope failure means the credential does
not permit the requested tags. Correct the credential scope through the human
administrator, then repeat the check. Enrollment runs through the owning Ansible workflow.

Installed mutation wrappers still require the active-controller guard.
The installer refuses a standby. After installation, repeat the handoff command
to verify completion. The guard
is an operational check, not a boundary against controller root compromise.
Revoke a failed or compromised controller's OAuth clients through the provider.
