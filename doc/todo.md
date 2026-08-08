Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-08-08 - dom0_known_hosts_file
`provision-box --box k001 --from 22 --to 22` failed before
contacting dom0 because `/tmp/klokast-dom0-known_hosts` was mode `0600` and
owned by another controller account. The play succeeded when
`dom0_known_hosts_file` was overridden to a smith-owned path under
`~/.cache/klokast`. Make the phase wrapper use a per-user, per-run known-hosts
path, as `platform-check` already does, instead of a shared `/tmp` file.

# 2026-08-07 - compiler must assign Tailscale ports for containers
As documented in `doc/platform-resource-control-plane.md:316` and referenced
from `apps/README.md:24``:
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




