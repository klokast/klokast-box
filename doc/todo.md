Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-08-22 - cloud-ops Ansible temporary directory ownership

Controller-side syntax checks found an old mode-`0700`
`/tmp/klokast-ops-ansible-local` owned by another account. The checked-in
configuration now uses `~/.ansible/tmp`. Do not restore a fixed shared path in
`/tmp`; it fails when a different controller account runs Ansible.

# 2026-08-22 - Terraform static-test execution locus

The airunner and active controller do not have Terraform. Source changes can
run Terraform `fmt`, `validate`, and `test` only on the trusted MacBook. Add a
credential-free CI job for these static checks. Do not install Terraform on
the controller only to work around this missing check.

# 2026-08-22 - catalog-driven cloud provider provisioning

Keep `cloud-providers.json` as the reviewed public identity catalog. Later
work can add pricing and region discovery, account registration, API-key
custody, and catalog-driven Terraform workflows.

# 2026-08-22 - add Tor as a shared service

The Platform should offer following ways to connect to internet and users:
- Tailscale (e.g. for inbound requests by family users)
- CloudFlare (e.g. for inbound static website requests by external users)
- local Access Point (e.g. for OS package downloads or traffic with torrent peers)
- local VPN client (e.g. for airunner)
- LAN (e.g. for user to play music)
- Tor (e.g. for bitcoin core)
- ...
Those connection modes should be defined in a standardized way (via which router zones,
how an app request those types, etc) so that new modes can be added to the Platform later.

# 2026-08-22 - migrate this `todo.md` to GitHub "issues" and feature requests

Or to another ticketing system, to industrialize the monitoring and resolution
of tickets.

# 2026-08-22 - protect against compromised `klokast.lock.json`

The `.engine.repository` field in `klokast.lock.json` defines the upstream repo.
So that the user can use his own fork of the repo, or a self-hosted clone. However,
how to protect from an attacker who would edit `klokast.lock.json` in the
(now untrusted) MacBook to point at a compromised malicious repo.
Maybe `klokast.lock.json` should be signed by the MacBook Secure Element or Keychain,
and there needs some monitoring to detect error in the signature check?
Or is there a better design, following cyber security best practices, to guarantee that
the installed deployment is pinned to an approved upstream?

# 2026-08-22 - upstream update process
Harden how upstream updates are handled, both in the trusted laptop and in the boxes:
- how the `.engine.repository` field in `klokast.lock.json` gets updated?
- is there an equivalent upstream commit version pin in the box controllers?
- How are deployed application pinned?

# 2026-08-21 - self-updating bootstrap helper continued old shell functions

`prepare-private-instance-bootstrap` previously ran `git pull --ff-only`
after Bash had loaded its functions. A pull from `eb4b89b` to `8ac344f`
updated the file on disk, but the process continued with the old controller
commit equality check. The helper now updates before any settings prompt and
uses `exec` when its commit changes. A two-commit Git fixture must continue to
prove that only the updated helper runs after a pull. A helper version from
before this correction still needs one separate `git pull --ff-only` to adopt
the restart behavior.

# 2026-08-21 - MacBook bootstrap wrappers require a real macOS integration run

The infra-agent host cannot run the interactive private-instance wrappers
because they require macOS, Tailscale access from the trusted MacBook, and
Apple CryptoTokenKit. Deterministic tests cover the separate controller and
approved-engine pins. The active controller can verify the sealed build and
the installed root wrapper. The human must still rerun
`prepare-private-instance-bootstrap` on the trusted MacBook to verify the
complete terminal and Touch ID path after a wrapper change.
More generally, the end-to-end User Experience of the various Klokast processes
must be mapped, reviewed, and improved. For example, the authentication process
on the truste MacBook, to avoid unnecessary fingerprint scans, and make it clear
to the user what he signs.

# 2026-08-16 - make touch ID sign in popups more explicit

At @doc/40-private-instance-bootstrap.md, step #8.1 , the current Touch ID popup only reads:
"ctcardtoken needs to authenticate to continue. touch ID to allow this."
It would be better if the popup title is Klokast instead of ctcardtoken. And it should show the intent
that is being signed. As the standard Touch ID popup cannot be customized, here is a proposal:
Create a signed, one-shot Klokast Approval.app. It would validate
the intent, display it, compute its digest, and sign the same in-memory bytes.
Apple’s native authentication API supports an application name and a clear
localizedReason. Apple documents this behavior
(https://developer.apple.com/documentation/localauthentication/lacontext/localizedreason).
The Security framework can also associate an LAContext with keychain
authentication. Apple documentation
(https://developer.apple.com/documentation/security/ksecuseauthenticationcontext).
This will probably require a signer migration or a new signature format because the
current OpenSSH security-key format contains extra flags and counter fields.

# 2026-08-12 - validate native Touch ID signing on the MacBook

The infra-agent host is Linux. It cannot execute Apple's `sc_auth`, access the
Mac Secure Enclave, or load `/usr/lib/ssh-keychain.dylib`. Deterministic tests
cover identity selection, profile custody, and OpenSSH command construction,
but they cannot prove the real biometric prompt on the current Mac.

The first real two-identity test found that Apple OpenSSH assigns
`id_ecdsa_sk_rk` to both P-256 identities during `ssh-keygen -K`. The second
key then asks to overwrite the first key handle. The current helper does not
use resident-key file download for a new profile. It loads all identities into
a private, short-lived Apple `ssh-agent`, selects the exact key by the SSH
fingerprint from `sc_auth`, and stops the agent after the operation. Validate
this revised path with both real identities on `og`. Do not accept an
overwrite prompt as an identity-selection mechanism.

The first revised run also found that Apple `ssh-add -?` groups short options
as `[-cDdKkLlqvXx]`. Capability checks must recognize `K` inside this group;
they must not require the separate text `-K` in the usage output.

The infra-agent also has no Go toolchain. Run Go tests and the exact build in
the networkless controller-managed sealed builder after the commit is pushed.

Before the old approval signer is retired, run both purpose-specific signer
setups and their controller verification round trips from `og`. Do not weaken
the native capability checks or adopt an identity that has no matching Klokast
profile metadata as a workaround.

# 2026-08-09 - NanoKVM Tailscale Serve is runbook-managed

The NanoKVM HTTPS listener presents a self-signed certificate with
`CN=localhost`. Tailscale connectivity and the operator grant can both work
while a browser rejects `https://oob.<tailnet>.ts.net/`. The active `oob`
device now uses a persistent Tailscale Serve configuration on TCP 443 that
proxies to `https+insecure://localhost:443` and supplies a valid certificate.

No Platform automation currently converges or verifies this Serve state after
a NanoKVM firmware update, factory reset, or Tailscale state replacement. Add
a narrow controller-owned reconciliation and verification path. Do not grant
`tag:ops` or `tag:airunner` direct web access as a test workaround. The device
also reported a Tailscale CLI version newer than the running daemon. Do not
restart Tailscale synchronously over Tailscale SSH; use a console-safe or
detached restart workflow.

# 2026-08-09 - remote Tailscale restarts must outlive Tailscale SSH

A synchronous `rc-service tailscale restart` on k001 stopped the daemon and
therefore stopped its own Tailscale SSH transport before the remote shell could
run the start action. The local console was required to start the service.

The dom0 Tailscale role now launches restarts as detached Ansible async jobs,
waits for reconnection, and verifies OpenRC, the backend state, and the daemon
version. Do not run synchronous Tailscale stop or restart commands through
Tailscale SSH. The incident also exposed a missing system DNS source. Managed
`udhcpc` hooks now publish WAN DHCP resolvers to `openresolv` without replacing
Tailscale-owned `/etc/resolv.conf`.

# 2026-08-09 - keep dom0 package resolution on its Alpine release branch

The k001 and k002 package-name comparison found `nghttp3` only on k002. Its
older `libcurl` package required it, while the newer `libcurl` on k001 did not.
The common dom0 repository setting used `latest-stable`, which had moved from
Alpine 3.23 to Alpine 3.24 during the rollout. An unrestricted upgrade would
therefore have performed an unreviewed release upgrade.

The dom0 APK policy now pins repositories to v3.23, refuses to run when the
live release does not match that branch, and reconciles all installed packages
to versions available from the branch. Keep the branch update coupled to a
reviewed Alpine release-upgrade canary. The dom0 Tailscale role also disables
Tailscale self-update so that it cannot bypass this package policy. Do not
restore `latest-stable` or enable Tailscale self-update on dom0.

# 2026-08-09 - validate dom0 dependencies against the boot repository

The first k001 dom0 policy canary showed that the live Alpine 3.23 repository
keeps `libcurl` as a dependency of `xen`. The earlier disposable dependency
check did not detect this relationship. The steady-state policy now keeps
`libcurl` out of `/etc/apk/world` but permits APK to install it as a transitive
runtime dependency. The reboot canary also showed that Alpine's diskless
initramfs adds `openssl` to the boot transaction for `modloop` signature
verification. The exact world must therefore include `openssl`. The same
canary also found that `lbu` resolves through the system path and is not
installed at `/sbin/lbu` on the live image.

Add a test that resolves the exact dom0 world against the same APK indexes and
package files that the diskless boot repository uses. Fail the test if a
forbidden installed package is in the resolved dependency closure.

# 2026-08-09 - move remaining guest image work out of dom0

The exact dom0 APK policy makes `curl`, `xorriso`, `sfdisk`, `kpartx`,
`e2fsprogs-extra`, `gzip`, and `util-linux` temporary RAM-only tools. However,
the router, Podman template, clone personalization, Debian image import, and
Klokast CLI builder bootstrap paths still execute some image work on dom0.

Move this work into a versioned short-lived builder VM. Resolve the bootstrap
dependency first: the current sealed builder template is itself first created
with dom0 partition and filesystem tools. A replacement must start from a
signed, controller-supplied builder template or another independently verified
artifact. Dom0 must then only create or clone LVs, attach them, copy approved
boot artifacts, render Xen configuration, and control guest runtime.

# 2026-08-09 - infra-agent host lacks Ansible CLI

The `vultr-ops` infra-agent checkout has Python and PyYAML but does not have
`ansible-playbook`. Repository YAML and Python tests can run locally, but
Ansible syntax checks must run from the active controller after the reviewed
commit is pushed. Do not install Platform controller tooling or private state
on the infra-agent host as a workaround.

# 2026-08-09 - k001 ops controller cannot reliably reach GitHub

During the exact APK-world rollout, `k001-ops` repeatedly failed to fetch
`https://github.com/klokast/klokast-box.git`; one controller convergence ended
after 135 seconds with curl error 28. Its clean checkout had to be fast-forwarded
from an exact Git bundle created on active controller `k002-ops`. The package
policy had already converged successfully, but the later public-checkout rehome
task could not finish, and the ops verification upstream probe is expected to
fail for the same reason. Investigate `k001-ops` DNS, outbound routing, and
firewall policy, and bound the rehome fetch. Do not weaken the requirement that
controller checkouts match public upstream history.

# 2026-08-07 - compiler must assign Tailscale ports for containers
As documented in `doc/platform-resource-control-plane.md:316` and referenced
from `apps/README.md:24`:
- Dedicated ports are needed only for containers running their own tailscaled
identity. Containers inheriting the Podman VM’s Tailscale identity need no additional port.
- Allocation is per independent Tailscale identity, not simply per application.
Two containers with separate identities need two unique ports.
Hence, the intended contract is:
1. Reserve a unique stable UDP port from 41644–41999.
2. Start that container’s tailscaled with --port=<reserved-port>.
3. Generate router policy permitting that UDP source port to WAN peers.
4. Permit destination UDP 3478 for STUN.
5. Verify direct connectivity.
However, this is currently documented but not implemented in the resource compiler.
The schema rejects the illustrative underlay declaration, and such app-owned identities
must remain disabled until compiler validation, allocation, firewall rendering, runtime
propagation, and verification are implemented together.
Also, the documentation currently models the port as explicitly declared:
```
underlay:
  compute: private-ingress-runtime
  udp_listen_port: 41644
```
It does not yet specify automatic compiler allocation. Architecturally, deterministic
compiler allocation and uniqueness validation would be preferable, allowing applications
to request an identity symbolically without choosing infrastructure port numbers themselves.
