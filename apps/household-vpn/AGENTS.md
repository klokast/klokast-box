# Household VPN

- Dedicated Alpine app VM: `<box>-household-vpn`, DMZ policy, `tag:vm,tag:household-vpn`.
- The router policy-routes household/admin Wi-Fi public egress through this VM.
- Requires box access policy selecting `household-wan-egress: vpn-egress`;
  AP discovery alone is not authority to deploy it.
- The VM is not a private app frontend; Tailnet access is for ops/control only.
- Runtime private VPN config is controller-local and must be passed through `household-vpnctl`.
