# Direct Overlay IPv6 Repair

Status: deferred. Do not run the existing repair executor after legacy-input
retirement.

The implemented executor requires a historical compatibility Plan and hashes
of `deployment.yml`, `platform-resources.yml`, and `controller-ha.yml`. These
live inputs are now retired. Restoring them only to satisfy this executor would
reintroduce a second authority and is not allowed.

Before live use, design and implement a new instance-only repair contract. It
must:

- use the active private instance, fresh source and recovery receipts,
  Authority State v5, a fresh Observation, and the exact sealed engine;
- select only the active `<box>-ops` network and one peer router;
- bind the Freebox gateway identity, API version, delegation slot, `/64`,
  router link-local next hop, Huawei prerequisite, and all preimages;
- keep the Freebox token root-only and use only the fixed local endpoint;
- preserve IPv4 and DERP access throughout the action;
- restore exact Freebox, router, controller, address, sysctl, and firewall
  state if verification fails;
- require a ten-minute Touch ID approval and a single-use nonce;
- refuse redirects, identity or prefix drift, occupied delegation slots,
  unsafe Huawei rules, partial action sets, and stale evidence;
- prove the final direct IPv6 UDP `41641` path without making direct transport
  a general Platform health requirement.

Manual Huawei work must keep the IPv6 firewall enabled and limit the rule to
the current peer-router `/128`, UDP, port `41641`. Do not use a whole `/64`,
DMZ, an unrestricted destination, or an assumed device-to-address binding.

The old design and acceptance notes are available in Git at commit `186cfa9`.
Track the replacement contract in `.run/todo.md` before implementation.
