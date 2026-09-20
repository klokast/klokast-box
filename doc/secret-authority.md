# Secret Authority

Klokast's target secret model is an action broker, not a chat-visible vault.
The AI/operator asks for a bounded action; deterministic controller code verifies
that request, uses root-held credentials internally, and runs the approved
workflow without printing raw secret values.

## Static-Site Pilot

The v0 pilot is `apps/static-site`.

- Durable GitHub authority is a GitHub App private key on the active
  `<box>-ops` controller under `/etc/klokast/secret-authority/`.
- `smith` may call only the checked root wrapper
  `/usr/local/sbin/ksa-static-site` through sudo.
- High-authority actions require a signed canonical intent verified with
  `ssh-keygen -Y verify` and the root-owned, scope-specific signer file.
- Cloudflare tunnel tokens are ingested once from stdin and stored root-only as
  app state. The token is released only into the child install process.
- GitHub installation tokens are minted only inside the wrapper and are passed
  only to the child `static-sitectl` process environment.

Root-side GitHub App configuration:

```sh
sudo install -d -m 0700 -o root -g root /etc/klokast/secret-authority
sudo install -m 0600 -o root -g root github-app.pem \
  /etc/klokast/secret-authority/github-app.pem
sudo tee /etc/klokast/secret-authority/github-app.env >/dev/null <<'EOF'
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=12345678
EOF
sudo chmod 0600 /etc/klokast/secret-authority/github-app.env
```

The GitHub App must be installed on the `klokast` organization with only the
permissions needed by the static-site pilot: repository administration write for
repo creation/deploy keys and contents write for the initial `bootstrap-repo`
push.

The MacBook uses three separate Apple-native Secure Enclave identities. The
static-site signer file contains only:

```text
human-static-site namespaces="klokast-secret-authority" sk-ecdsa-sha2-nistp256@openssh.com AAAA...
```

The private-instance signer is stored separately as `human-private-instance`.
Platform Apply uses `human-platform-apply` and the separate
`klokast-platform-apply` signature namespace.
Use `klokast-dev/runbooks/15-touchid-secret-authority.md` to create and install
all signers. Do not write a signer file by hand.

The controller paths are:

```text
/etc/klokast/secret-authority/allowed-signers-static-site
/etc/klokast/secret-authority/allowed-signers-private-instance
/etc/klokast/secret-authority/allowed-signers-platform-apply
```

Generate an approval intent from the controller checkout:

```sh
ansible/bin/secret-authority intent static-site install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  > intent.json
```

The human signs `intent.json` on the trusted MacBook with the static-site
Secure Enclave key:

```sh
klokast-dev/bin/sign-secret-authority-intent \
  --purpose static-site \
  --intent intent.json
```

Run the approved action from `<box>-ops` as `smith`:

```sh
ansible/bin/secret-authority static-site install \
  --box boxa \
  --domain www.klokast.ai \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --approval-intent intent.json \
  --approval-signature intent.json.sig \
  --signer-id human-static-site
```

To ingest the static-site tunnel token, use the MacBook-side wrapper. It
generates the controller intent, displays the approval review, signs with the
static-site Secure Enclave key after Touch ID, prompts for the token with echo
disabled, sends the token only through stdin, and verifies redacted status:

```sh
klokast-dev/bin/ingest-static-site-cloudflare-token \
  --controller boxb-ops \
  --box boxa \
  --domain www.klokast.ai
```

Redacted status is safe to inspect:

```sh
ansible/bin/secret-authority static-site status --redacted
```

Do not store root provider credentials or app tunnel tokens on `vultr-ops`.
That host is an authoritative AI runner, not the Platform credential custodian.

## Private Instance Bootstrap

Private-instance bootstrap uses a separate, temporary GitHub App. Do not reuse
the static-site App. Configure the temporary App with these repository
permissions only:

- Administration: read and write;
- Metadata: read;
- Contents: no access.

The human first creates the exact empty private organization repository
`FAMILY/klokast-instance` in the GitHub web interface. Do not add a README,
license, `.gitignore`, template, branch, or commit. Install the App on only
that repository. Never grant it access to all organization repositories or an
unrelated repository. From the trusted workstation, install its credential on
the active controller:

```sh
klokast-dev/bin/install-instance-github-app \
  --pem /path/to/private-key.pem \
  --app-id 123456 \
  --installation-id 12345678 \
  --controller auto
```

The installer sends the PEM through standard input. It stores the credential
under `/etc/klokast/secret-authority/instance-bootstrap/`. It does not put a
token or PEM on the command line.

Prepare the controller read key as `smith` on the active controller:

```sh
ansible/bin/platform-instance prepare \
  --repo-owner FAMILY \
  --repo-name klokast-instance
```

The command prints a redacted repository hash and the public-key fingerprint.
It keeps the private key root-only under `/etc/klokast/private-instance/`.

Run one wrapper command for each high-authority action from the trusted
MacBook. First verify and register the human-created repository. Then register
the controller read-only key:

```sh
klokast-dev/bin/run-private-instance-action register-repository
klokast-dev/bin/run-private-instance-action register-read-key
```

The wrapper gets one 10-minute intent from the controller and displays it. The
human checks the exact action, repository, current controller commit, approved
engine commit, key fingerprint when present, and expiry before approval. The
wrapper asks before it calls the dedicated Apple CryptoTokenKit identity.
Touch ID protects the non-exportable Secure Enclave key. The wrapper transfers
only the intent and signature and runs only the approved action. It never
copies the local approval profile to the controller or an airunner.

The approved engine and the current public controller checkout are separate
pins. The controller checkout can be newer. It must be clean, and the approved
engine must be in its Git history. Before an approval or seed operation, the
MacBook wrappers also verify the exact sealed build directory, builder receipt,
binary hash, and binary version with `platform-instance verify-engine`.
The closed approval intent schema v2 uses `repo_head` for the current
controller commit and `engine_commit` for the approved sealed engine. This
intent version is separate from Instance Specification v1.

`register-read-key` keeps the GitHub key only when GitHub confirms
`read_only: true` and an authenticated Git query proves that the repository has
no refs.

Use the MacBook helper to run the guided controller-private values setup,
generate the initial files with one verified sealed-builder output, transfer
the seed, and verify its initial Git state:

```sh
klokast-dev/bin/prepare-private-instance-worktree
```

The helper shows the detected DNS name, member login, roles, and reviewed
topology only in the trusted terminal. It does not copy those values into
arguments or journals. It does not commit, add a remote, or push. Its
underlying controller operations use these fixed `platform-instance`
interfaces. The values file and destination use fixed paths below the
controller private root:

```sh
ansible/bin/platform-instance configure-values \
  --engine-commit ENGINE-COMMIT \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION

ansible/bin/platform-instance seed \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION \
  --values /home/smith/private/klokast/init-values.json \
  --destination /home/smith/private/klokast/instance-seed

ansible/bin/platform-instance validate-candidate \
  --engine-commit ENGINE-COMMIT \
  --build-dir /var/lib/klokast/builds/klokast-cli/ENGINE-COMMIT/OPERATION \
  --require-bootstrap \
  <klokast-instance.json
```

`validate-candidate` accepts at most one 64 KiB instance document through
standard input. It copies the owner-only unborn seed to a temporary private
directory, replaces only `klokast-instance.json`, and checks it with the sealed
binary. Bootstrap mode requires the unborn seed. Later publication requires
complete active Instance Specification v1 authority and absent retired input
paths. It returns the checked Git tree and removes the temporary directory. It
does not use an Observation, create a Plan, or change the seed or values file.

Review and publish the transferred worktree with the MacBook helper:

```sh
klokast-dev/bin/publish-private-instance
```

Use `publish-private-instance --check` to run the same sealed contract and
authority validation without a commit, push, or publication.

The helper commits and pushes `main` with the human private-repository
identity. It can also publish a later staged `klokast-instance.json` update
when remote `main` still equals the local base commit. The sealed seed is the
validation base only before the first private commit. Later publication uses
the exact synchronized deployment checkout, a fresh source receipt, and the
engine selected by the private lock and its immutable activation receipt. It
also verifies that the recorded previous engine can read the rollback form.
Neither controller state grants Git write authority. Do not commit or push
from an airunner. Do not use the temporary GitHub App to push content.

After the first push, use the GitHub web interface to remove this repository
from the temporary App installation. GitHub does not permit an installed App
to have zero selected repositories. If `klokast-instance` is the only
repository in the organization, uninstall the organization App installation
instead. Keep the App definition and credentials until the signed retirement
action succeeds. Then run:

```sh
klokast-dev/bin/run-private-instance-action retire-bootstrap
```

Retirement fails unless the App can no longer list the repository or the App
identity proves that the saved installation was uninstalled. It also requires
the dedicated App to have no other installation, the anonymous Git read to
fail, and the deploy key to read `refs/heads/main`. On success, it deletes the
temporary App PEM and IDs from the controller. The human can then delete the
dedicated GitHub App through the GitHub web interface.

Synchronize the deployment checkout and create a fresh source receipt:

```sh
ansible/bin/platform-instance sync \
  --repo-owner FAMILY \
  --repo-name klokast-instance
```

The checkout is `/home/smith/private/klokast/instance`. Its push URL is
disabled. The receipt is below `/var/lib/klokast/instance-sources/` and is valid
for one hour from its fetch time. Pass its exact path to `ansible/bin/platform-plan` with
`--instance-source-receipt`. Redacted status is:

```sh
ansible/bin/platform-instance status
```

Run the same synchronization after each human-published instance update. The
controller remains a read-only consumer of the private repository.

## Controlled Engine Promotion

After the active controller builds one later canonical commit with the sealed
builder, run this command on the trusted MacBook:

```sh
klokast-dev/bin/promote-private-instance-engine \
  --controller BOX-ops \
  --new-engine-commit COMMIT \
  --build-operation OPERATION \
  --check
```

Remove `--check` only after review. The helper reads the old engine from the
private lock and shows the complete engine and schema-transition diff locally.
It permits a metadata-only change or the closed reversible legacy Instance v1
transition. It gets a 10-minute controller intent that binds the transition
and uses the existing
`human-private-instance` Touch ID signer. The human MacBook creates and pushes
the private commit. The controller stays read-only and activates only the
approved candidate tree.

The helper selects a clean controller public checkout at the reviewed public
`main` commit. It checks `~/src/klokast/klokast-box-update-candidate` first,
then the fixed deployment checkout. Build and prepare candidate source in the
separate checkout while the fixed checkout remains at the active engine.
Preflight and signature verification still use the installed root authority.
Source selection does not fetch, change either checkout, or grant approval.
After activation, converge the fixed deployment checkout and installed wrappers
to the exact activated engine before normal Platform operations resume. Do not
pull the candidate into the fixed checkout to make preflight pass.

Use this command for a forward rollback:

```sh
klokast-dev/bin/promote-private-instance-engine --controller BOX-ops --rollback
```

Rollback accepts only the previous engine in the active activation receipt.
It never rewinds or force-pushes private `main`. Promotion receipts are below
`/var/lib/klokast/engine-promotions/`. Activation receipts are below
`/var/lib/klokast/engine-activations/`. Both are immutable root-owned evidence
that is readable by `smith` and contains no private repository name, private
path, or private JSON.

## Standing VM update authority

The [Instance contract](klokast-instance-specification.md#shared-vm-update-intent)
defines the narrow update policy. Policy schema validation and signed policy
activation are implemented. The VM replacement executor is not yet implemented.
Discovery output cannot authorize VM replacement.

The `ksa-apply vm-update-policy prepare` action uses fresh Plan v8 verification
evidence and current Controller Toolchain v8. It prepares a separate closed
`klokast.vm-update-policy-intent.v1`. The existing trusted-workstation
`platform-apply` identity signs this exact intent. Execution verifies the
signature and consumes its nonce before rechecking evidence. Root-owned
activation receipts and the active pointer stay under
`/var/lib/klokast/updates/executor`; the private checkout stays read-only.
Activation does not broaden Plan v8, Plan v9, or general signed Apply rules.
Each future replacement operation must verify current
policy, controller authority, exact artifacts, targets, and fresh preflight
evidence. Revocation or a changed engine or toolchain must block new operations.
An already authorized operation must finish safely or recover.

Policy status and resume verify the archived signature with the current allowed
signer set, sealed engine, installed toolchain, active controller, Authority
State, and sealed current Instance input. The activation Observation is not
reused as execution evidence. Policy changes, engine promotion, signer
revocation, or authority changes require new activation. Local pause is a
root-owned restriction and can still be set when policy validation fails.
One fixed installation lock at `/var/lib/klokast/updates/operation.lock`
serializes activation, pause, resume, box provisioning, Platform resource Apply,
and future replacements. Root owns the file and its parent directory; the
`smith` group can lock the file but cannot replace it. The lock grants no
execution authority. Acceptance of a standing policy is not a successful VM update.
The root-only `ksa-apply vm-update-policy source-status` action returns the
current signed policy, activation checksum, engine and private commits,
Authority State checksum, and local pause state to the active controller. It
accepts no caller-selected evidence. A resume record names one activation;
it cannot enable a later activation. This reader gives selection evidence,
not permission to execute a replacement.

The discovery collector has inspection authority and writes non-authoritative
evidence as `smith`. Package indexes and VM facts are untrusted input. Native
APK verifies repository signatures; index parsing never extracts archive paths
or runs package scripts. The isolated build VM contains package-script
execution and has no production identities, secrets, or data. The future
root executor has VM lifecycle and retained-data authority. It therefore needs
fixed operations, root-protected records, exclusive execution, fencing, and
independent local recovery before activation.

For an automatic build, `ksa-apply vm-update-release import --operation-id ID`
holds the installation lock, checks current signed policy, and revalidates the
no-application release against the full package manifest and build tests. It
stores one immutable record under
`/var/lib/klokast/updates/executor/releases/ACTIVATION/RELEASE.json`.
`prepare --auto` invokes this action for a new or unchanged template. This
record binds evidence; it is not an accepted VM assignment. The replacement
executor must check the artifact bytes on dom0 again before use.

`ksa-apply vm-update-adoption prepare --qualification PATH` accepts only a
complete v7 report for a selected no-application VM. It binds the current
policy activation, classified report, independent management receipt, and
exact old Xen source in a closed one-hour intent. The trusted-workstation
signer approves that intent through the existing `execute` signature path.
Execution consumes the nonce, refreshes all VM inventory, repeats
qualification, and compares the current old disk and boot identities. The
root controller sends a fixed old-only request to dom0. Dom0 publishes the
recorded old assignment without stopping the guest. Root-owned authorization
and operation records remain under
`/var/lib/klokast/updates/executor/adoption-preflights/` and `adoptions/`.
An interrupted operation requires its persistent record and current authority
to reconcile; a standby copy cannot execute it.
`ksa-apply vm-update-adoption reconcile --nonce NONCE` can write a missing
controller receipt only after it verifies the archived signature, current
policy, nonce, fresh qualification archive, and exact dom0 assignment. It
cannot create the dom0 assignment.
The detached controller recovery manifest includes protected update records,
archived signatures and nonces, the authority bindings, and the exact
qualification and build files referenced by those records. It does not
activate a policy or an assignment on a standby controller.

The offline retained-data copy library has only guest-local filesystem
authority. A copy request and its receipt cannot authorize disk attachment,
writer shutdown, or adoption. The future signed executor must derive and verify
its mappings, backup evidence, fencing, and disk identities before it exposes
data to the disposable networkless guest. Treat filesystem contents as
untrusted input. Keep that parser and copy boundary out of dom0.
The discovery storage assessment has inspection authority only. Catalog
matches and path checks do not authorize copying, deletion, or adoption.
Its unprivileged report cannot replace fresh executor checks or sealed
Instance retention intent.

The root-only `ksa-apply vm-retention-status` reader obtains current adopted
inventory-source evidence from the sealed checker. It compares both private
file hashes with that evidence before projecting logical retention. It then
rechecks the sealed source, active authority, and exact file bytes. It accepts
no caller-selected path, binary, catalog, or observation. An inactive
controller, changed source, dirty checkout, or unavailable sealed engine
blocks the read. Its result is intent evidence only; it grants no copy,
deletion, adoption, or replacement authority. The unprivileged retention report
compares this result with discovery. A future executor must repeat the checks
with fresh evidence at execution time.

The optional application component test has no production authority. The
controller can stage only its fixed public catalog image. A separate read-only
capsule enters the networkless test VM, where the fixed adapter loads and runs
the image as a synthetic unprivileged user. App code can compromise at most
that disposable VM and its synthetic disks. Dom0 handles opaque bytes and
bounded evidence only. A successful component receipt cannot establish that a
production deployment has the same image, configuration, or retained data.
The signed executor must independently prove these facts before replacement.

A local pause may only restrict execution. Resume must revalidate the current
activated policy. Neither a pause file nor a discovery report can increase
authority. Machine observations must not replace approved desired state.

## Platform Apply

Only the active `<box>-ops` controller can execute a Platform Apply action.
The human reviews and signs one canonical intent on the trusted MacBook. The
root executor verifies the active-controller fence, exact Plan, sealed engine,
controller toolchain, private source receipt, source recovery receipt,
Observation, signature, expiry, and single-use nonce. An airunner cannot sign
or execute the action and does not receive these inputs.

An Apply intent is valid for exactly one hour. Engine-promotion intents have
a maximum lifetime of one hour. This does not change enrollment-key lifetimes.
Archived ten-minute Apply intents remain valid historical evidence, not
requests that can be executed under the new lifetime policy.
The executor consumes its nonce
before it starts the operation checks. A used intent cannot be retried. Create
fresh evidence and obtain a new approval after a failure. The executor writes
the final machine-readable result to stdout. Bounded, non-secret progress and
errors go to stderr.

Source receipts and Observations expire one hour after collection. Approval
does not extend either deadline. Verification preflight reports the earliest
evidence deadline and requires at least 15 minutes to remain for execution.
This reserve is not a runtime guarantee. Retirement revalidates the exact Plan
after the complete consumer matrix, before it offers approval or proceeds
with execution. Expired evidence requires a new evidence set and Plan.

If sealed Plan revalidation fails, the controller retains its private stdout
and stderr in a root-only `revalidation-*` directory under
`/var/lib/klokast/apply-preflights/`. These diagnostics have indefinite
retention. User-facing errors show only known diagnostic codes and safe labels,
not arbitrary private planner output. Content changes and expiry are distinct
failures.

The MacBook helper can repeat the exact signed request after success when
`--prove-replay-refusal` is selected. It accepts only one exact controller
diagnostic:

- the nonce was already used; or
- the intent expired before the replay.

The helper reports which condition refused the replay. It does not treat an
SSH error, transport failure, malformed result, or unknown diagnostic as proof
of replay protection. Expiry validation remains before nonce validation. Do
not weaken the lifetime or reorder validation to obtain a preferred message.

All six desired-state groups use Instance Specification v1. Plan v8 is the
closed verification-only contract for Controller Toolchain v7 or v8. New
operations must use the current exact v8 receipt. Plan v9 with Controller
Toolchain v8 is the closed legacy-retirement lifecycle contract.
After retirement, its `verify` phase is the supported signed proof that the
old inputs remain absent and current consumers remain unchanged. Earlier Plan
versions and explicit compatibility inputs exist only for recovery, tests, and
reading historical artifacts. They are not a normal authority fallback.

The direct overlay IPv6 repair uses intent v2 and executor v2. It accepts only
a two-box Plan v8 and removes all legacy-input hashes from its signed contract.
It binds the current instance source, Authority State v5, Observation, sealed
engine, current toolchain, Freebox selection, Huawei prerequisite, and exact
host preimages. Direct transport is a repair-specific result. It is not a
general Platform health requirement. See
[Direct Overlay IPv6 Repair](../klokast-dev/runbooks/47-overlay-ipv6-direct-repair.md).

Keep these controller-held artifacts even when public acceptance notes are
removed:

- execution receipts and consumed nonce records;
- authority states and Plans;
- source and source-recovery receipts;
- audit logs and policy recovery material;
- the root-only legacy-input recovery archive.

Apply preflight evidence has indefinite local retention. No scheduled task,
controller convergence, or Apply command can delete or move it. A preflight is
permanent evidence if its nonce was consumed, it has an execution receipt, an
Authority State history record refers to it, or its recovery result is not
resolved.

An expired unsigned or check-only preflight can enter a future archive only
through a separate reviewed workflow. That workflow must copy exact bytes to a
root-owned, content-addressed archive, record and verify a manifest, and keep
the source until deletion has separate human authorization. The current
implementation does not delete or archive preflights. The installed
`README.retention` file records this rule at the evidence root.

The public transition narrative is available in Git at commit `186cfa9`.
Current verification commands are in
[Instance Authority Verification](../klokast-dev/runbooks/53-instance-only-verification.md).
