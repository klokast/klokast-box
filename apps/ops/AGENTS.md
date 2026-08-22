The `ops` controller is the trusted infrastructure automation environment.
The active controller is one Alpine VIRT VM named `<box>-ops`. A separate
cloud or in-Platform airunner can open the approved remote terminal to it, but
that runner is not the controller or the private-state custodian.

# Conditions

- OpenAI Codex may run on one or more approved cloud or in-Platform runners
  after each runner passes live connectivity and boundary checks.
- `<box>-ops` is for TCB automation and private state. The current transitional
  runner container on the same VM must not mount `/home/smith`, `/etc/klokast`,
  `/var/lib/klokast`, deploy keys, private registries, OAuth files, and broker
  state. It remains a persistent Control TCB authority. The
  `<box>-ops-airunner` container is a supported steady-state placement.

# Deployment model

- one Alpine VIRT VM on the selected master box, in the control-only `ops`
  zone, not in the app-facing `bak`, `dmz`, `iot`, or `usr` zones.
- local accounts:
  - `smith`: owns private state, SSH keys, Tailscale OAuth/wrappers,
    Tailnet policy apply, `platform-resources apply`, builders, and dom0/router
    authority.
  - `minion`: runs app installs and `platform-resources show`/`verify`,
    without access to infra credentials.
- failsafe: the approved cloud infra-agent remains bootstrap and break-glass
  access until
  `<box>-ops` is reproducibly recoverable from Git plus private state.

# Implementation

- `ansible/bin/provision-ops-vm --box <box>`
- `ansible/bin/converge-ops-airunner --box <box> --instance candidate` stages
  a blue-green runner without replacing the canonical instance
- `ansible/bin/converge-ops-airunner --box <box> --instance canonical
  --require-candidate-ready` runs only after candidate Mosh verification
- `ansible/bin/retire-ops-airunner-candidate --box <box>` runs only after
  canonical Mosh verification
- `ansible/playbooks/65-vm-ops.yml`
- `ansible/playbooks/68-ops-airunner.yml`
- `ansible/roles/ops-controller`
- `ansible/roles/ops-airunner`
- `ops-controller` clones the public `klokast/klokast-box` repository over
  HTTPS into `/home/smith/src/klokast/klokast-box`. It does not give `smith`
  a write credential for that public repository.
