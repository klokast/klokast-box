# Tailscale wrappers on the controller

The active `<box>-ops` controller owns Tailscale credentials and installed
root-owned wrappers. The `ops-controller` Ansible role installs these wrappers
and their narrow privilege rules. Use `ansible/bin/converge-ops-controller`
from the controller source checkout to converge that configuration.

See [credential setup](40-tailscale-wrapper-setup-policy.md) for OAuth installation
and rotation, and [device lifecycle](41-tailscale-wrapper-devices.md) for stale
machine cleanup. Public topology and private Instance ownership are defined in
[Architecture](../../doc/architecture.md#overlay-management-plane).
