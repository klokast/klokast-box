# Controller Tailscale credential setup

Each `<box>-ops` controller stores its own OAuth credentials root-only under
`/etc/klokast/`. The controller Ansible role installs the root-owned wrappers
and their privilege rules. Run Platform operations as `smith` there.
See [Tailscale automation](../tailscale/AGENTS.md) and
[Architecture](../../doc/architecture.md#overlay-management-plane).

## Credentials

Prepare two distinct scoped OAuth clients for each controller through the
human administrator. Do not reuse the active controller's clients on the
standby. Install independent credentials during initial setup:

In the Tailscale admin console, open **Settings**, then **Trust credentials**.
For each client, click **New credentials**, select **OAuth**, and enter the
**Description**. Tailscale refuses `/` in the description. Create the clients
with these permissions:

| Local file | OAuth scopes | Tag selection |
| --- | --- | --- |
| `tailscale-policy.env` | Policy file **Write** (`policy_file`) and Auth Keys **Write** (`auth_keys`) | `tag:ops` |
| `tailscale-devices.env` | Devices Core **Write** (`devices:core`) | `tag:ops` |

Keep the dependent scopes that Tailscale requires for Policy file access.
The installed Platform policy makes `tag:ops` an owner of the managed
enrollment tags, including `tag:airunner`. Select that owner tag on each
client. See [OAuth client setup](https://tailscale.com/docs/features/oauth-clients#setting-up-an-oauth-client)
and [scope requirements](https://tailscale.com/docs/reference/trust-credentials#scopes).
Name the clients for their controller and purpose, for example
`boxb-ops policy and enrollment` and `boxb-ops devices`. Save each new client ID
and secret before closing the creation page; the secret is shown once.

Each local input file has these fields:

```text
TAILNET_ID="-"
TS_OAUTH_CLIENT_ID="your-oauth-client-id"
TS_OAUTH_CLIENT_SECRET="your-oauth-client-secret"
```

An OAuth client belongs to one tailnet, and `TAILNET_ID="-"` selects it. The
tailnet ID from **Settings**, **General** is also valid. With `-`, a client of
another tailnet is accepted by Tailscale, so
`ansible/bin/controller-credentials status` checks that the devices client lists
this controller's own node.

Keep the files private and outside Git. Use the existing human secret-storage
path; do not send credentials through chat or command arguments.

## Install or rotate

[`kk setup --controller BOX-ops`](../../klokast-dev/README.md#controller-credentials)
guides these steps and runs the installer below. To use the installer
directly from the operator MacBook, send the private input files to the explicit
controller during initial setup or credential rotation:

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
The installer verifies the destination controller identity and development mode.
It permits an unfenced standby. Planned switching checks both controllers before
changing placement. The guard
is an operational check, not a boundary against controller root compromise.
Revoke a failed or compromised controller's OAuth clients through the provider.
