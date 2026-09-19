- Follow Ansible best practices.
- Run playbooks with `-vv` for high verbosity.
- Avoid nested playbooks and roles
- Preserve the trust boundary in `doc/architecture.md`: app playbooks may
  verify and install services, but TCB-owned playbooks apply topology,
  Tailnet, dom0, router, firewall, and privileged builder changes.
- Check `klokast-box/runbooks/` for relevant manual procedures that were run during early development and could inform automation
- Naming convention:
  - kebab-case
  - playbook filename includes index number to show execution order
  - names of verification-only roles are suffixed: `-verification`

# Directory structure

ansible
├── ansible.cfg
├── ansible.md
├── bin                               # Orchestration scripts to run a flow of playbooks
│   └── platform-resources            # Compile/apply/verify Git-approved Platform resources
│   └── platform-guest                # Manage durable shared Xen guest runtime intent
│   └── platform-map                  # Discover current Platform state and write runtime map artifacts
│   └── platform-check                # Run read-only live infrastructure health checks across map, dom0, router, Podman, ops, and resources
│   └── platform-check-remote         # Dispatch Platform health checks from infra-agent hosts to the Ansible controller
│   └── platform-image-build          # Build app OCI images on the active ops controller and load them onto target VMs
│   └── platform-builder              # Build the Klokast CLI in a short-lived networkless Xen guest
│   └── archive-codex-sessions        # Pull retiring Codex host conversation records into controller-private state
│   └── bootstrap-dom0                # Bootstrap a blank host into diskless dom0
│   └── decommission-box              # Decommission one box and wipe its SSD
│   └── nanokvm-virtual-media         # Manage NanoKVM media/service operations over root SSH
│   └── provision-ops-vm              # Clone, enroll, and converge one in-Platform ops controller VM
│   └── converge-ops-controller       # Converge an existing in-Platform ops controller VM
│   └── converge-ops-airunner         # Converge the AI runner container on an ops controller
│   └── provision-box                 # Provision one box through dom0, router, and Podman VMs
│   └── reinstall-box                 # Load ISO, decommission, wait for bootstrap, then provision
│   └── render-node-inventory         # Render temporary name-agnostic inventory for one box
├── collections
├── inventory
├── overview-playbooks                # For each playbook: purpose, roles it calls, tasks in these roles
│   ├── playbooks-1x-bootstrap.md     # Bootstrap the Platform, taking as input the host running Debian Live, and turn it into an Alpine Linux host that runs diskless (from RAM), uses the SSD for persistence across reboots, and is reachable via Tailscale
│   ├── playbooks-2x-dom0.md          # Install the Xen hypervisor and setup the `dom0` domain and the bridges
│   ├── playbooks-3x-router.md        # Add the router VM, including routing tables, `nftables`, `dnsmasq`, and `dhcpd`.
│   ├── playbooks-4x-podman.md        # Add the shared Podman container engine VMs to the box: `bak`, `dmz`, and `iot`.
│   ├── playbooks-6x-ops.md           # Add the optional in-Platform `<box>-ops` controller VM.
│   ├── playbooks-7x-platform-map.md  # Collect Platform map facts and run read-only Platform checks.
│   ├── playbooks-8x-apps.md          # Install applications onto the platform. Most apps use Podman containers; selected apps can request per-user Debian PVH app VMs with Docker inside the VM.
│   └── playbooks-9x-decommission.md  # Decommission a box
├── playbooks
└── roles

# Remote task execution and cleanup

- Use synchronous tasks with SSH connection reuse and pipelining for short work.
  Use bounded async tasks for long work or a service restart that breaks its own
  connection. High latency alone is not a reason to use async for every task.
  Choose a poll interval that limits round trips without delaying recovery.
- Check the installed become plugin before relying on pipelining. The controller
  uses checksum-pinned `community.general` 12.6.2 with Ansible core 2.20.
  Managed Alpine inventory enables `allow_pipelining` only for `nopass` groups.
  The upstream docs label the option 12.4.0, but the inspected Galaxy 12.4.0
  archive lacks it. Use the qualified artifact, not that version label.
  Pipelining does not remove staging required by file transfers or async tasks.
- Every async task must specify a time limit, poll interval, registered result,
  and `ansible_async_dir`. Use a private directory owned by the execution account.
  Use volatile storage for transient results where possible. Use an operation
  directory when recovery needs persistent evidence. Do not put job caches in
  persisted account homes by default.
- With `poll > 0`, Ansible removes the job cache after it observes a completed
  result, including a command failure. Do not add duplicate cleanup tasks.
- With `poll: 0`, the launching workflow owns result collection and cleanup.
  Save the returned job ID, wait with `async_status`, and use `mode: cleanup` for
  that exact ID after completion. Use the same account and async directory for
  launch, status, and cleanup. Put cleanup in `always` so a completed command
  failure does not bypass it. Propagate the original failure.
- Do not delete results when completion is uncertain, the target is unreachable,
  or the controller stops. Report the job ID and result path for reconciliation
  before retrying the operation. Cleanup does not stop a running job. A wrapper
  timeout is not proof that all child work stopped. Never remove a shared tree
  or use a wildcard to clear job results.
- The producing workflow must also own its module staging, helper files, and
  temporary output. Capture command output in the task result instead of a fixed
  `/tmp` log. Follow [the shell temporary-file rules](../doc/shell.md). Keep
  recovery journals, required backups, and audit evidence under their separate
  retention rules. Do not treat them as async cache files.
- Test changed async flows with completed success, command failure, and uncertain
  completion. Verify that cleanup leaves unrelated records intact. Test loss of
  connection or controller execution when the flow depends on recovery from it.
  Never rely on a later blanket cleanup playbook to repair routine task hygiene.

The native cleanup regression tests run on the active controller, without
restarting Tailscale or touching guest state:

```sh
python3 -m unittest discover -s ansible/tests -p 'test_async_cleanup.py' -v
```

See the Ansible documentation for [async cleanup](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_async.html)
and [doas pipelining](https://docs.ansible.com/projects/ansible/latest/collections/community/general/doas_become.html).

The controller role installs the pinned collection in a root-owned versioned
directory. It preserves Alpine's packaged collection. To prepare a candidate
toolchain before engine promotion, run these checks on the active controller
from the clean candidate checkout:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i localhost, \
  ansible/playbooks/66-ops-ansible-toolchain.yml
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vvv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/66-ops-ansible-pipelining-verify.yml --limit HOSTS
```

Check that `ansible-doc -t become community.general.doas` resolves to
`/usr/local/share/klokast/ansible/12.6.2/`. Keep the verbose qualification log on
the controller: each remote module must show `Pipelining is enabled`, root UID
`0`, and no module upload. The unchanged approved checkout continues to use its
old configuration until signed engine activation. Re-run the native async
tests with the new collection selected. Normal controller convergence uses the
same pinned installation tasks after promotion.

# Automation flow

MacBook initiates, ops orchestrates, ISO only enrolls, Ansible mutates:

- Human: owns accounts, physical actions, destructive confirmations.
- MacBook: starts the workflow, holds local Terraform state or bootstrap config, shows status.
- GitHub repo: source of truth for automation, inventory, runbooks, ops bootstrap code.
- Ops server: authoritative deployment controller after creation.
- NanoKVM: remote boot/media/power/console path.
- Bootstrap ISO: disposable first-contact environment.
- Ansible playbooks: perform all durable machine changes.

Secrets and durable state are centralized while still giving the human a single command to start the first-box bootstrap.

Step-by-Step Flow :

  1. Human prepares external accounts
      - Creates or confirms GitHub, Hetzner, and Tailscale accounts.
      - Creates required Tailscale auth keys: tag:ops, tag:oob, tag:bootstrap, tag:dom0, tag:vm.
      - Connects NanoKVM to the mini PC, HDMI, USB, power, and network.
      - Either enrolls NanoKVM into Tailscale as <box>-oob, or gives the MacBook/ops a direct LAN route to it.
  2. Human starts from MacBook
     ```
     kk box up boxa
     ```
     The MacBook becomes the launcher, not the long-term controller.
  3. MacBook validates local prerequisites
      - Checks brew tools: Terraform, Ansible, Tailscale, SSH, Git.
      - Checks MacBook is logged into Tailscale.
      - Checks GitHub repo checkout is clean enough to use.
      - Checks Hetzner token is available.
      - Checks NanoKVM target config exists for boxa.
  4. MacBook creates the ops server
      - Runs Terraform from klokast-ops.
      - Creates Hetzner Ubuntu server, firewall, SSH bootstrap path.
      - Runs the ops Ansible bootstrap playbook over public IPv4 SSH.
      - Enrolls ops into Tailscale as tag:ops.
      - Creates users neo and codex.
      - Installs Ansible, Git, Tailscale tooling, tmux/mosh, and required wrappers.
  5. Ops server connects to GitHub
      - Generates or uses a GitHub deploy key.
      - Human may need to approve/add the deploy key in GitHub.
      - Ops clones klokast-box.
      - GitHub remains the source of truth; ops runs checked-in automation, not ad hoc copied scripts.
  6. Ops installs secret wrappers
      - Target model: ops stores scoped Tailscale OAuth material root-only and
        exposes narrow wrappers that mint short-lived, single-use auth keys
        after validating purpose, hostname, and tag set.
      - Transitional model: any reusable auth-key files remain root-owned TCB
        debt under `/etc/tailscale-auth/` and must not be readable by app
        users or app sandboxes.
      - target steady-state automation splits authority:
          - `smith` may call infrastructure wrappers and run
            `platform-resources apply`;
          - `minion` may run app installs and `platform-resources show` or
            `verify`, but cannot read raw secrets or mutate infrastructure.
      - transitional codex/automation can only call narrow wrappers like:
          - ts-authkey-mint
          - ts-authkey-bootstrap
          - ts-authkey-dom0
          - ts-authkey-vm
          - ts-authkey-ops
          - ts-authkey-infra
          - ts-authkey-infra-agent (legacy)
      - Raw keys are not committed, not stored on the ISO, and not stored in inventory.
  7. Ops prepares the bootstrap ISO
     For the first box, there is no existing yii-bak builder, so use one of two paths:
      - preferred v1: ops builds the generic Debian bootstrap ISO
      - later: MacBook downloads a signed release artifact
     The ISO is generic: no box name, no auth key.
  8. Ops uploads ISO to NanoKVM
      - Resolves NanoKVM as <box>-oob over Tailscale or configured LAN address.
      - Uploads ISO and checksum to NanoKVM storage, either from the builder
        container over SSH or through `ansible/bin/nanokvm-virtual-media --upload-url`.
      - Verifies checksum on NanoKVM.
      - Selects/mounts the ISO through `ansible/bin/nanokvm-virtual-media --load`
        when NanoKVM SSH is available.
      - Otherwise tells human exactly what to click in NanoKVM UI.
  9. Human boots the mini PC
      - Powers on the box or confirms NanoKVM power action.
      - Ensures it boots from the ISO.
      - Uses NanoKVM console only if boot/device selection fails.
  10. Bootstrap ISO starts
      - Gets DHCP.
      - Publishes kk.local / klokast.local on LAN.
      - Starts the Go onboarding portal.
      - Does not format disk or install Alpine yet.
  11. Human enters bootstrap data
      - Opens http://kk.local/.
      - Enters box name: boxa.
      - Pastes bootstrap Tailscale auth key.
      - ISO runs:
      ```
        tailscale up --ssh \
          --hostname=boxa-bootstrap \
          --advertise-tags=tag:bootstrap \
          --auth-key=...
      ```
  12. Ops detects bootstrap readiness
      - Ops is already waiting in a polling loop.
      - It watches tailscale status --json.
      - It requires exact match:
          - hostname boxa-bootstrap
          - online
          - has tag:bootstrap
      - Then it probes Tailscale SSH as root.
  13. Ops starts Ansible phase 2
      - Runs:
        ```
        ansible/bin/provision-box --box boxa
        ```
      - The wrapper renders temporary inventory for the requested box name.
      - Playbooks run from ops against boxa-bootstrap.
  14. Ansible installs dom0
      - Phase 10 verifies bootstrap access.
      - Phase 11 wipes/repartitions SSD after explicit destructive gate.
      - Phase 12 stages Alpine diskless boot files.
      - Phase 13 verifies staged boot state.
      - Phase 14 unloads the NanoKVM ISO before reboot. The wrapper keeps a
        manual detach fallback for NanoKVM SSH outages.
      - Box reboots from SSD into Alpine diskless.
  15. Ansible completes identity handoff
      - Box comes back temporarily as boxa-bootstrap.
      - Phase 20 configures base dom0 state.
      - Phase 21 switches Tailscale identity to boxa-dom0 with tag:dom0.
      - Phase 22 verifies steady-state dom0.
  16. Ops continues Platform convergence
      - Runs next playbook groups:
          - Xen host setup
          - router VM
          - backend/dmz/iot VMs
          - VM Tailscale enrollment
          - Podman host setup
      - initial apps if requested
      - Each identity is created by Ansible on the target machine using ops-side wrappers.
  17. Optional: create the in-Platform ops controller
      - A separately provisioned `<cloud>-ops` host can remain an approved
        Codex/OpenAI runner and break-glass bootstrap host.
      - Run `ansible/bin/provision-ops-vm --box <box>` for the chosen master
        box.
      - The new `<box>-ops` VM owns infrastructure credentials and private
        state through `smith`; app work uses `minion`.
  18. MacBook only reports status
      - kk box up boxa streams logs or shows the current phase.
      - If MacBook sleeps/disconnects, ops continues.
      - Re-running kk box up boxa attaches to the existing ops-side run or resumes from recorded state.
