- Follow Ansible best practices.
- Run playbooks with `-vv` for high verbosity.
- Avoid nested playbooks and roles
- Preserve the trust boundary in `doc/architecture.md`: app playbooks may
  verify and install services, but TCB-owned playbooks apply topology,
  Tailnet, dom0, router, firewall, and privileged builder changes.
- Check `klokast-box/runbooks/` for supported manual setup and console recovery procedures
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
│   └── bootstrap-dom0                # Compatibility entrypoint for dom0 phases of provision-box
│   └── decommission-box              # Decommission one box and wipe its SSD
│   └── nanokvm-virtual-media         # Manage NanoKVM media/service operations over root SSH
│   └── provision-ops-vm              # Clone, enroll, and converge one in-Platform ops controller VM
│   └── converge-ops-controller       # Converge an existing in-Platform ops controller VM
│   ├── converge-ops-airunner         # Converge a legacy AI runner container during migration
│   └── airunner                      # Provision, verify, migrate and retire native Alpine runners
│   └── provision-box                 # Provision one box through dom0, router, and Podman VMs
│   └── reinstall-box                 # Load ISO, decommission, wait for bootstrap, then provision
│   └── render-node-inventory         # Render temporary name-agnostic inventory for one box
├── collections
├── inventory
├── lib
│   └── app_support                   # Shared application helper catalog and future Python helpers
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

See [provisioning entrypoints](../doc/platform-deploy.md#dom0-provisioning-entrypoints)
for the shared runner and controller prerequisites.

See the [shared helper catalog](lib/app_support/README.md) before adding or
reusing application deployment helpers. It owns their interface and delivery
instructions, including installation through the owning Ansible tasks.

# Remote task execution and cleanup

- Use synchronous tasks with SSH connection reuse and pipelining for short work.
  Use bounded async tasks for long work or a service restart that breaks its own
  connection. High latency alone is not a reason to use async for every task.
  Choose a poll interval that limits round trips without delaying recovery.
- Check the installed become plugin before relying on pipelining. The controller
  selects the latest stable `community.general` from Galaxy metadata and checks
  the published archive checksum. It verifies the installed files and requires
  the doas `allow_pipelining` option before selecting the collection. Managed
  Alpine inventory enables this option only for `nopass` groups.
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

The controller role installs the resolved collection in a root-owned versioned
directory. It preserves Alpine's packaged collection. To install and verify
the pinned toolchain, run these checks on the active development controller from the
clean source checkout:

```sh
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vv -i localhost, \
  ansible/playbooks/66-ops-ansible-toolchain.yml
ANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -vvv \
  -i ansible/execution-inventory/hosts \
  ansible/playbooks/66-ops-ansible-pipelining-verify.yml --limit HOSTS
```

Check that `ansible-doc -t become community.general.doas` resolves to
`/usr/local/share/klokast/ansible/current/`. Keep the verbose qualification log on
the controller: each remote module must show `Pipelining is enabled`, root UID
`0`, and no module upload. Run the native async tests with the resolved collection
selected. Controller convergence uses the same installation tasks.

# Automation flow

The MacBook provides operator tools. The active `<box>-ops` controller runs
Ansible as `smith` from `~/src/klokast/klokast-box`. Private state and
infrastructure credentials stay on that controller. Cloud AI runners use the
remote-terminal path to the controller; they do not perform Platform operations locally.

1. The operator prepares the physical box, NanoKVM, and external accounts.
2. The controller builds the generic, secret-free Debian bootstrap ISO and
   Alpine seed through `ansible/bin/bootstrap-live-iso`.
3. The operator boots the ISO and supplies the box name and a short-lived,
   single-use bootstrap enrollment key through the onboarding portal.
4. The controller runs `ansible/bin/provision-box --box BOX`. Its phase runner
   owns inventory, operator gates, logs, and cleanup.
5. Bootstrap phases prepare Alpine diskless boot. Dom0 phases establish its
   identity, local console recovery access, Xen, and storage.
6. Router phases install and verify the protected router assignment.
7. Shared guest phases clone versioned templates and finalize networking,
   identity, and rootless Podman.
8. The optional controller VM is provisioned with `ansible/bin/provision-ops-vm`.
9. Application installation follows the owning application's instructions and
   the desired declarations in Instance.

See [Platform deployment](../doc/platform-deploy.md),
[bootstrap artifacts](../apps/bootstrap-iso-debian/release-artifacts.md), and
[application catalog](../apps/README.md) for the supported workflows.
MacBook application commands use the
[`kk` interface](../klokast-dev/README.md#application-commands-with-kk).

# Resource compiler implementation

`ansible/bin/platform-resources` owns argument parsing and command dispatch.
Its modules are in `ansible/lib`:

- `platform_resource_model.py` validates declarations and loads topology and manifests.
- `platform_resource_guests.py` compiles application guests and managed devices.
- `platform_resource_compiler.py` builds plans, resource ownership records, and grants.
- `platform_resource_runtime.py` owns controller checks, operation locks, and execution.

Dependencies point from runtime to compiler, from compiler to guests, and from
all three to model. Pass the repository root explicitly when a function reads
repository inputs or runs repository tools. Import helpers from their owning
module; do not load the command as a library. The installed command uses the
controller checkout's modules. Controller convergence checks these imports
before it installs the command.

The authority contract remains in [Resource compiler](../doc/architecture.md#resource-compiler).
Run the local regression suites with synthetic inputs and mocked execution:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s ansible/tests -p 'test_platform_resource*.py'
```

Tests share fixtures in `ansible/tests/platform_resource_test_support.py`.
The model, compiler, runtime, and CLI suites test their own module boundaries.
The input-absence integration suite requires `ansible-inventory` on `PATH`:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s ansible/tests -p 'test_instance_input_absence.py'
```

It runs the current Instance readers, compiler, and application wrappers in
disposable checkouts. Test processes supply controller identity and validated
Go projections. Actual Ansible inventory parsing runs; remote execution and
build commands use a closed test environment. The `controller-wrapper-commands-v2`
report includes refusals for retired commands and registry overrides. It is
local dependency evidence, not a Platform health check.
