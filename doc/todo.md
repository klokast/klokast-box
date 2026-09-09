Write below the difficulties encountered during work.
Include context to allow an AI agent to later solve the issues.
Format of the first line: `# yyyy-mm-dd - title`

# 2026-09-09 - Inventory source adoption completed

Engine `cf8cf5f` passed signed adoption, fresh signed verification, exact replay
refusal, and final unchanged-setting checks. Final Plan `540bae24` has six
verification-only groups, no remaining legacy-owned setting groups, no
continuing legacy authority, and no pending adoption action. The
[acceptance record](../klokast-dev/runbooks/52-inventory-source-adoption.md#acceptance-record-2026-09-09)
holds controller evidence. The host-override and fact-cache corrections below
passed real controller acceptance. Keep their failed historical baselines.

Evidence inspection must use each artifact's defined comparison: parse both
complete registry documents before comparing values, because YAML formatting
differs. Plan hashing omits `plan_sha256`; Authority State hashing uses an
empty hash field. Applying the wrong rule causes a false audit refusal.
Both checks passed with the defined rules; no source or setting repair ran.

Source adoption does not authorize removal of comparison inputs or recovery
files. `legacy_removal_ready` remains false. Continue from the
[current work queue](upstream-instance-target-architecture.md#current-work-queue).
Do not automatically promote this acceptance documentation commit.

# 2026-09-09 - Cached router facts changed the inventory approval hash

Unsigned preparation on engine `87cd6d7` passed router verification but refused
its final input recheck. Two immediate diagnostic comparisons later matched,
so that check did not explain the failure. After `c1324ea` promotion and tool
installation, comparison of the saved evidence identified cached router facts
in the inventory output. Router verification had populated uptime, free
memory, interface facts, and other observations between the two comparisons.
The host-override correction was valid, but the approval hash still included
these runtime observations.

Inventory comparison and the retained inventory reader now explicitly select
Ansible's process-local memory cache. They do not load the controller's saved
setup facts. All declared inventory variables remain checked, including names
that resemble facts; only the two existing provenance fields are excluded
from normalization. Normal playbooks keep their configured fact cache.

The regression test writes and changes a real temporary Ansible fact cache,
proves that ordinary inventory output changes, and requires identical output
from the source reader with declared values preserved. Run this test with
controller Ansible; the runner skips it when Ansible is absent. Keep the
earlier evidence and create a fresh baseline after corrective promotion.

# 2026-09-09 - Normal legacy inventory lost a generated host override

After promotion of engine `87cd6d7` and matching Toolchain v6 installation,
the read-only baseline found that the normal reader reported
`ops_airunner_enabled: false` for k002. The retained base lists sample hosts;
the real k002 host enters through a generated box file. Parsing the base alone
inside the reader omitted `host_vars/k002-ops.yml`. No runner convergence,
router configuration, or source adoption ran.

The correction derives both boxes from checked controller identity and parses
the retained base with both generated box graphs. This loads the existing host
overrides before the reader returns. It resolves the normal MagicDNS suffix
before Ansible flattens group variables. Controller and source records are
rechecked, and temporary files are removed on success and failure. The adopted
reader still uses only the sealed instance graph.

The comparison now runs the actual reader through Ansible in both source
states, with normal generated box files and with the adopted graph alone.
Only protected status responses are fixtures. Rendering, host-variable loading,
group merging, and the source reader are real. The root executor independently
compares these saved results. Keep this gate in every sealed private review;
testing a script that merely prints the desired graph did not cover the bug.
The [inventory runbook](../klokast-dev/runbooks/52-inventory-source-adoption.md)
records the stopped acceptance and the completed corrective acceptance.

# 2026-09-08 - Ansible omits empty groups from its inventory output

The first sealed private comparison stopped because Ansible retained child
references to empty `usr`, `ungrouped`, and legacy `jump` groups but omitted
their objects from `--list`. The comparison now treats those missing output
objects as empty. The sealed input graph still requires every group object and
rejects missing references or cycles. Rechecking the saved controller output
showed equal variables and memberships for all 14 instance hosts.

The full review must exercise both Ansible static parsing and its script
plugin. Normal inventory source failure must stop Ansible, even when another
generated inventory is valid. The new default configuration makes any
unparsed source fatal. Toolchain bootstrap uses explicit retained inventory
arguments; normal operation has no missing-helper fallback.

# 2026-09-08 - Inventory migration must compare Ansible's merged variables

The generated box graph alone does not contain all execution settings.
Ansible merges generic group policy and legacy host variables, including the
controller-container runner enablement override. The inventory source design
therefore requires equality of effective host variables and complete role
membership for every instance-derived host. Only inventory-file and directory
provenance can be excluded. Extra legacy sample hosts remain recorded as
excluded from normal selection, with no contact or deletion.

The offline sealed renderer and comparison helper are the first implementation
checkpoint. They leave Plan v6, Authority State v4, normal readers, and source
ownership unchanged. Validate them with real controller Ansible and private
inputs before adding the source executor and switching normal consumers.
The [inventory design](upstream-instance-target-architecture.md#inventory-and-runner-source-adoption-design)
defines that remaining implementation and acceptance.

# 2026-09-08 - Final registry Plan still requires inventory and runner work

Registry source adoption is `live-verified`. Signed verification, exact replay
refusal, final unchanged-setting checks, protected evidence, and final Plan
passed. The [acceptance record](../klokast-dev/runbooks/51-registry-source-adoption.md#acceptance-record-2026-09-08)
holds the controller evidence. Recognized DERP on k001 passed; it requires no
network repair for this milestone.

The final Plan has no retained legacy setting actions. It still assigns
`execution_inventory` to `legacy_engine_inventory` and emits one runner
adoption action with prior authority `none`. Do not describe that action as
a remaining legacy-owned registry group. The inventory consumer audit found
static base inventory, generated per-box hosts, and a box-specific runner
enablement override. A complete replacement must cover normal consumers,
effective host variables, exact limits, and recovery inputs before the Plan
can remove continuing inventory authority. The
[current queue](upstream-instance-target-architecture.md#current-work-queue)
already authorizes this scope development.

# 2026-09-08 - Allow documentation updates in the Mac acceptance checkout

The supplied registry resume command required the Mac `HEAD` to equal the
active engine commit. It stopped at that local assertion before contacting
the controller. This check also rejects the published acceptance documentation
commit, although all approval code is unchanged. The Mac commit was not
reported, so confirm compatibility through the diff instead of assuming its
value. Require a clean Mac checkout and permit differences from the active
engine only under `doc/` and `klokast-dev/runbooks/`. Refuse other differences
with a clear diagnostic. Keep the controller's exact engine, toolchain, and
input checks. The [registry runbook](../klokast-dev/runbooks/51-registry-source-adoption.md#mac-checkout-for-verification-resume)
contains the corrected complete command.

# 2026-09-08 - Resume registry acceptance after completed adoption

The human interrupted acceptance after registry adoption and preparation of
the verification request. Restarting the two-step command ran adoption again
and correctly refused `input changed: active-authority-state`. Inspection
confirmed the exact Authority State v4 transition and a successful adoption
receipt. Desired-state files, controller markers, and router configuration
matched the original baselines. No signed verification receipt existed; the
old verification nonce was unused and its request had expired.

Resume at verification after inspecting the source and receipts. Keep the
original baselines and all old requests. The controller-held resume helper
creates a separate attempt, checks current settings against those baselines,
and refreshes evidence before unsigned verification preparation. It accepts
no operation selector and cannot execute adoption. Do not rerun the original
two-step command, overwrite its verification arguments, or relax input and
expiry checks. The [registry runbook](../klokast-dev/runbooks/51-registry-source-adoption.md#interrupted-acceptance-2026-09-08)
records this historical checkpoint. Fresh signed verification and exact replay
refusal subsequently completed the milestone; its final acceptance record is
linked above.

# 2026-09-07 - Test promotion after publishing optional instance fields

After the registry candidate was published, the Mac promotion helper refused
`inactive-apps` with `private Instance v1 document has no supported promotion
shape`. Its automatic transition selector still required the earlier root
field set. The existing metadata-only transition and controller validator
already preserve optional registry fields; the selector now accepts the
declared optional field. Unknown root fields and lossy legacy transitions
remain refusals. Both sealed-engine checks remain required.

The regression test executes the actual embedded Mac candidate generator,
checks exact file preservation, and passes its envelope to the controller
validator. Private review must also generate that exact envelope and run the
installed unsigned promotion preflight before asking the human to retry.
The failed Mac attempt did not publish a private commit or change source
ownership. Its controller public checkout had already advanced for promotion;
use the explicit controller contact while installed Apply tools still match
the active engine.

# 2026-09-07 - Registry validator must accept pending Plan actions

The first live registry preflight refused before router verification or source
publication. The sealed Plan correctly kept an airunner migration outside the
selected registry group with executor `unimplemented_action`. The root registry
validator expected the nonexistent spelling `unimplemented`. The correction
uses the planner's exact vocabulary and does not execute that pending action.

The Python registry fixture had omitted pending actions, and its signed-file
test bypassed the Plan parser. It now includes the airunner action and parses
the stored Plan during preparation and execution. The Go registry test also
requires that pending action. Keep the actual sealed private Plan parser check
in the review gate; synthetic fixtures alone did not cover this deployment.
The published private registry candidate remains valid. Preserve the refused
attempt and its baselines, promote the correction, then capture new baselines
and refresh evidence before signed adoption.

# 2026-09-07 - Registry source adoption must replace normal readers too

A source-record change alone would leave the compiler, lifecycle tools, and
Immich registry writers using the old YAML file. The grouped implementation
adds the verified `platform-registry` reader and source guards before legacy
writes or dependent runtime changes. Explicit compiler compatibility mode is
read-only. Tests must stub the selected source or select that comparison mode;
do not add a production fallback when the installed helper is missing.

The signed-file test caught a missing new-intent entry in the shared nonce
dispatcher. Registry intents now use the same single-use Plan binding as the
existing signed workflows. Tests cover protected archives, readable runtime
copies under umask 077, source publication, receipt failure, and exact replay.
The [registry runbook](../klokast-dev/runbooks/51-registry-source-adoption.md)
defines the remaining live acceptance. Execution inventory is still a separate
continuing source; source adoption does not authorize legacy-file removal.

# 2026-09-07 - Registry migration needs a compatible rollback checker

Private publication validates candidates with the active sealed engine and
its previous engine. Adding fields in one promotion and then publishing them
would fail the previous engine's closed schema. The
[registry compatibility checkpoint](upstream-instance-target-architecture.md#compatibility-checkpoint)
preserves this check: promote schema support with unchanged private values,
then make that engine the rollback checker for the later consumer engine.
The checkpoint refuses deployment if its new fields are present. Do not
remove the rollback check or silently discard new fields in a reverse map.

The first sealed candidate check found two saved empty placement strings:
the disabled builder target and one disabled app's secondary target. The
initial schema required a box name at both paths. The corrected schema
preserves empty strings as unselected targets and still rejects nonempty
references to unknown boxes. Do not invent a target or remove these fields.

An isolated-checkout syntax check also needs an explicit `ANSIBLE_CONFIG`
pointing to that checkout's `ansible/ansible.cfg`. Without it, Ansible cannot
find the repository roles. The builder wrapper already sets its own config.

The scope audit also found that omitted disabled apps were reported as one
derived action. This hid cleanup placement, devices, VM and user bindings,
resource preselection, and saved privileged-build controls. The planner now
reports each field separately with continuing legacy ownership. One disabled
app has no current public manifest; typed inactive configuration must remain
independent of launch support. No app absence implies data deletion.

The root instructions reference the missing `apps/STORE.md`. The supported
application index is in [apps/README.md](../apps/README.md#supported-apps).
No application installation was part of this checkpoint.

# 2026-09-07 - Expired controller identity request and acceptance coverage

The first human controller identity request expired before execution.
Inspection found the original source unchanged, both matching preparation
nonces unused, no execution receipt, and no runtime residue. The new attempt
kept the original baselines and old archives and used fresh evidence. Signed
adoption, fresh signed verification, and exact replay refusal then passed.
The [controller identity acceptance record](../klokast-dev/runbooks/49-controller-identity-source.md#acceptance-record-2026-09-07)
holds the evidence references and completed `live-verified` result.

Request lifetime validation precedes nonce consumption. Therefore a refused
expired request does not imply that its nonce was consumed. Inspect both
source and nonce evidence before choosing the next action. Preserve expired
requests and prepare a fresh request; do not extend the signed lifetime or
reuse an expired signature. Complete each human approval before its displayed
`expires_at` time.

The MacBook Apply helper uses the supplied explicit controller contact.
Successful Apply execution therefore does not prove that the separate
MacBook automatic controller resolver works. Keep that read-only acceptance
check explicit. The human subsequently confirmed automatic resolution,
explicit resolution, and the dispatch dry run on the MacBook. All selected
the unchanged active controller and completed acceptance. The missing
`ssh-askpass` notice recurred, but both fresh
signatures succeeded; it did not block this source migration.

# 2026-09-07 - Controller identity migration implementation

The human authorized continued migration across scopes without another
scope-selection question. The
[controller identity decision](upstream-instance-target-architecture.md#114-controller-identity-source-migration)
now closes the resolver, source versions, signed action, and explicit recovery
path. Its [runbook](../klokast-dev/runbooks/49-controller-identity-source.md)
owns installation and acceptance. The earlier review below is historical.

The full Python suite under umask 077 exposed an existing Plan test fixture
that requested directory mode 0750 in `mkdir` but did not restore bits masked
by the caller's umask. The fixture now explicitly sets its intended mode.
Production evidence permissions remain strict. Review also found that the
shared Go state structure could accept the new v3 adoption field as `null`
in a historical v2 document. Both v2-reading entry points now reject that
field even when null, and a regression check covers both loaders. No
historical artifact is changed. Controller identity tests use
real signatures and temporary files under umask 077, including readable
runtime configurations, protected archives, consumed nonces, source
publication, and receipt failure. Read-only live inspection found the
expected configured active and standby roles.

The next source adoption still requires exact sealed validation, engine
promotion, matching tools, and signed live acceptance. No source or role
changed during implementation. The
[current queue](upstream-instance-target-architecture.md#current-work-queue)
owns continued migration work.

# 2026-09-07 - Next migration review found controller source dependencies

The final connectivity Plan has four matched controller identity fields and
one derived controller-placement field. These findings do not mean normal
controller dispatch already consumes the instance. `ops-controller-ha`
still loads its identity set from the legacy HA registry. Its automatic
resolver is also used by MacBook helpers. Authority State v2 rejects any
group other than Tailnet and box connectivity. A new source group therefore
needs explicit versioning and an end-to-end consumer design.

The placement finding is synthesized by the planner. Its legacy-looking path
is not proof of a controller-selection field in the legacy deployment file.
Also, the HA guard reports legacy active behavior when its marker is absent;
new migration verification must require a configured marker and check both
controller roles. Do not use that fallback as adoption evidence or change
recovery behavior without a design.

The review initially invoked the installed guard by name, but the remote
non-login shell did not include its directory in `PATH`. Using the checked-in
absolute path, `/usr/local/sbin/klokast-controller-guard`, completed the
read-only check. No Platform setting changed.

The [next-scope proposal](upstream-instance-target-architecture.md#114-controller-identity-source-migration)
records the recommended five fields and the remaining consumer, bootstrap,
recovery, and acceptance design gates. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the next decision. This review did not implement or approve another
source transition.

# 2026-09-07 - Controller-box source acceptance completed

Source-only adoption, fresh signed verification, exact replay refusal, and
unchanged-setting checks passed for engine `cc68fc6`. Both box connectivity
groups and Tailnet now use the instance. The
[controller-box acceptance record](../klokast-dev/runbooks/48-controller-box-connectivity-source.md#acceptance-record-2026-09-07)
contains the immutable receipts, final Plans, and controller-held evidence
directory. The completed first-box record below remains historical evidence.

The acceptance work encountered these resolved preparation and inspection
issues. None required a router change or a change to the sealed implementation:

- The first controller syntax command named the nonexistent
  `62-ops-controller.yml`. The check passed with the actual playbook,
  `67-ops-controller-converge.yml`. No play ran during the failed check.
- The first baseline helper treated the instance's `boxes` object as an
  array. It failed before router contact. Iteration over the object keys
  supplied the correct box IDs.
- Combining the base and generated inventories also selected example
  routers. Both real router reads succeeded, but example names did not
  resolve. An explicit limit to the two instance-derived router names fixed
  the evidence collection. The refused attempt remains in
  `baseline-example-hosts-refused/` under the acceptance directory.
- The first source-evidence helper assumed a narrow set of Git origin URL
  formats and refused the valid `ssh.github.com` origin. Using repository
  identity from the authenticated source receipt fixed the refresh. The
  refused attempt remains in `adoption-20260906T232948Z/`.
- Protected archive inspection through `sudo` was refused by the controller's
  command policy. The existing `doas` path permitted the read-only checks.
  Archive ownership and modes stayed unchanged.

The runbook records the corrected syntax, inventory, and source-identity
steps. No implementation change was needed during live acceptance. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the next legacy-scope decision. Live rollback, re-adoption, direct-IPv6
repair, and legacy removal remain deferred.

# 2026-09-06 - First-box forward verification acceptance completed

The human promoted combined corrected engine
`86b8c7e5e14ad84bda2fae0c685a6c3fe1b7606d` through the existing MacBook
Touch ID workflow. Private commit `06800dd6ddc01ab159f9d580073d3871a5ebf04e`
contains only the expected engine metadata transition. The controller
verified the sealed Go build, complete cleanup, all ten installed toolchain
components, source synchronization, source recovery, and Authority State v2.
All 501 Python tests and the controller Ansible syntax check passed.

The human approved one `verify_instance_authority` request for the exact
five-scope k001 connectivity group. Authenticated router configuration,
service, route, firewall, and reachability checks passed. The immutable
execution receipt has result `verified`, recovery result `not_needed`, and
hash `edf3c7e502e5196d91a957adc5bf2f9bc85b62832d4fe807e6b49d9ad826fc71`.
The MacBook helper repeated the exact signed request and completed only after
it received `Apply intent nonce was already used`. The final Plan has hash
`9dc2cf955e04386d859529d4abb039f93da57f98fe9013fa43ecfa98a71d9381`.
It is valid and deployable, keeps k001 and Tailnet verification-only, and
keeps k002 on `legacy_platform_resources`.

All seven saved setting-source and desired-state hashes remained unchanged.
The active Authority State stayed
`6cdc48ae5cf3027774b654d8dc1cc41f256172e70ceca882e9b529e608a27a23`.
No network setting, Tailscale service, application, application data, second
box source, or legacy file changed. The first-box milestone is now
`live-verified`. The Platform is ready for the next migration decision.
Moving the controller box's settings still requires a separate explicit
decision.

Live rollback and re-adoption remain unverified and deferred. Direct-IPv6
repair also remains deferred. Neither blocks continued development. Keep all
saved recovery inputs and immutable evidence. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining order.

# 2026-09-06 - Builder output parent blocks controller receipt access

The sealed build for the signed verification path correction completed its
Go tests, compilation, and Xen cleanup. The controller then failed to read
the installed receipt. Its new engine-commit directory had mode `0700` and
owner `root:root`, while the final operation directory was installed with
the requested `root:smith` ownership and mode `0750`.

`platform-builder` used one BusyBox `install -d` call for the complete output
path. Only the explicit final directory received those owner and mode flags.
The implicit commit parent inherited `umask 077`. A controller-local test in
a temporary directory reproduced the implicit parent's mode `0700` without
root access or any Platform change. The same omission affected installation
of failed-build diagnostics.

The builder now explicitly installs each output parent as `root:smith` with
mode `0750` before creating the new operation directory. It still refuses to
overwrite an existing operation. It also verifies the installed successful
result as the controller before reporting success. The regression test uses
the real install utility and files under `umask 077`, replacing only privilege
escalation and ownership changes. It covers successful results, bounded
failure diagnostics, every parent mode and owner request, content, and
overwrite refusal. It failed against the old code and passes with the fix.

The completed intermediate build and its failure evidence remain saved; that
engine was not promoted. The combined engine passed a second sealed build,
the controller read its installed receipt, and the human promoted it as
`86b8c7e` on 2026-09-06. Do not loosen the caller's umask or edit installed
receipts. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining acceptance steps.

# 2026-09-06 - Signed box verification reads the protected preflight archive

The human completed both fresh preflights, entered the approval phrase, and
signed the verification intent with Touch ID. Engine `bd7d044` then refused
`verify-box-access` with `PermissionError` for the saved
`apply-preflights/<nonce>/effective-registry.yml`. At 11:19 UTC, all baseline
setting-source and desired-state hashes were unchanged. The controller
checkout still selected that engine and the private commit was unchanged.
No execution receipt was stored. This failure occurs after nonce consumption;
the failed nonce must remain used. A new attempt needs a new signed intent.

Preflight archives intentionally have a root-only parent and files. During
signed execution, `box_revalidate` creates a controller-readable runtime
copy and proves that its bytes and compiled values equal the approved inputs.
The verification branch then used `binding["effective_registry_path"]`,
which points to the root-only archive, instead of the revalidated
`current["effective_registry_path"]`. The helper runs as `smith` and cannot
traverse the archive. The earlier umask correction repaired runtime directory
modes but did not repair this path selection. Preflight-only live testing and
the earlier unit tests did not cover the handoff during signed execution.

The correction passes the revalidated runtime path to the verification helper.
Archive permissions, exact byte and hash checks, signature verification,
nonce handling, and network behavior remain unchanged. Adoption, rollback,
and restoration already stage their protected inputs before invoking the
controller helper. Their live rollback and re-adoption tests remain deferred.

The new regression test executes the verification branch with real saved
files, runtime staging, exact saved-byte comparison, nonce consumption, and
receipt storage under `umask 077`. External Plan/build validation, signature
verification, ownership changes, and the router command are test doubles.
It failed against the old path and passes with the correction. It covers a
verified receipt, a helper failure, refusal of altered saved bytes, cleanup,
unchanged archive modes, and nonce replay refusal after success or failure.
All 500 Python tests passed for this correction. Its sealed Go build passed,
but the receipt installation exposed the builder parent-mode issue above.
The combined engine passed 501 Python tests, the sealed Go build, matching
tool checks, signed promotion, forward verification, and replay refusal on
2026-09-06.

The MacBook printed a missing `ssh-askpass` notice, but then completed the
signature. The controller accepted that signature before this path failure.
The notice was not the cause of this refusal. Preserve the old evidence and
use the [current work queue](upstream-instance-target-architecture.md#current-work-queue)
to resume with the corrected engine. Do not widen archive permissions or
change network settings to force success.

# 2026-09-06 - First-box approval cancelled before Touch ID

The MacBook preparation batch produced fresh valid evidence. Both controller
preflights passed, and the second displayed the exact first-box
`verify_instance_authority` intent. The MacBook helper then printed
`approval cancelled` at its text confirmation prompt. This branch means that
the line read was not exactly `approve platform apply`. The transcript does
not show which line was read or whether cancellation was intentional.

At 10:54 UTC, the controller had no execution receipt for either prepared
intent. All saved setting-source and desired-state hashes remained unchanged,
and the public and private checkout commits still matched the acceptance
baseline. The fresh-evidence correction passed the real MacBook checks;
this cancellation occurred before the Touch ID signer was invoked.

A local pseudo-terminal simulation used the unchanged MacBook helper and
Python launcher with stub SSH and signing commands. A queued blank line
reproduced immediate cancellation. With no queued line, the helper waited
for the exact phrase and then reached the test signer. This is a possible
input cause, not proof of what occurred on the MacBook. Do not supply the
approval phrase through automation or treat cancellation as authorization.

The revised Python command clears queued terminal input after `--check`
with `termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)`, then starts
`--prove-replay-refusal`. A second local pseudo-terminal simulation confirmed
that this discards the queued blank line and waits for a new typed phrase.
SSH and signing remained stubs in these simulations. The revised command
passed on the MacBook at 11:17 UTC: the human entered the phrase and Touch ID
produced a signature. This does not establish what caused the earlier input
cancellation. The command does not change the controller, the signing helper,
or any approval check. For an unintended cancellation,
the human must start a new approval and enter the exact phrase at its prompt.
The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining signed verification and replay gates.

# 2026-09-06 - Delayed approval used expired first-box evidence

The MacBook `apply-platform-intent --check` twice refused the saved Plan with
`Plan v3 inputs changed during exact revalidation`. A controller-side rerun
of the same sealed planner exposed diagnostic `fetched-at.stale`: the source
receipt was more than 30 minutes old. The receipt was created at 08:53:39 UTC,
and the observation at 08:55:21 UTC. At diagnosis, 10:21:15 UTC, both exceeded
their 30-minute limits. The earlier controller preflight had passed while
they were fresh. All baseline setting-source and desired-state hashes, the
public engine commit, and the private commit were unchanged. No signing or
execution occurred in these failed checks.

The handoff prepared evidence before the human was ready to sign, then gave
the human fixed paths that could expire during the wait. Prepare evidence
when the human starts the MacBook workflow. The controller preparation batch
uses the existing observation, source synchronization, recovery, toolchain,
and Plan commands. It checks the fixed commits, baseline hashes, and exact
verification-only groups, retains each attempt separately, and returns its
new approval arguments. The MacBook then runs `--check` and
`--prove-replay-refusal` with that same set. Source, observation, intent,
signature, and replay checks remain unchanged. No engine rebuild or
promotion is needed for an evidence refresh. See the
[first-box runbook](../klokast-dev/runbooks/46-box-connectivity-authority.md#3-create-plan-v3).

The first batch trial rejected the refreshed observation reference in the
read-only substrate action. The batch now checks that reference against the
new observation. It requires all other action fields to remain unchanged.
This comparison applies only between separate evidence attempts; root Apply
still revalidates each stored Plan exactly.

The corrected batch produced a valid deployable Plan, and the controller
preflight passed at 10:32 UTC under `umask 077`. The baseline hashes remained
unchanged. This confirms that fresh evidence resolves the unsigned refusal;
signed verification and replay refusal remain the live acceptance gates.

The root revalidation error does not distinguish expired evidence from a
content mismatch. A future error-reporting correction should expose the
bounded diagnostic code without private input content. This does not block
acceptance with fresh evidence. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining acceptance gates.

# 2026-09-05 - Box preflight work directory loses group access under umask 077

The first-box forward-verification attempt stopped in the controller
`platform-apply preflight` used by the MacBook check workflow. The error was
`ksa-apply: installed platform-resources failed to compile the old registry`.
It occurred before router verification or creation of an approval intent.
No signed verification or live replay test ran. The active source record,
private desired-state files, legacy files, and both checkout commits remained
unchanged. The first-box milestone remains `implemented`.

Before this refusal, wrapper convergence and all ten Controller Toolchain v3
component checks passed against engine `d2c7c39`. Source synchronization,
activation validation, source recovery, and a fresh deployable Plan v3 also
passed. The Plan selected first-box and Tailnet verification and retained
the controller box's old registry. All 497 Python tests and the controller
Ansible syntax check passed. The existing sealed build recorded successful
Go tests and build output.

The controller preflight ran with `umask 077` to protect private evidence.
`ksa-apply.new_box_work` requests mode `0750` for its per-request directory,
but `Path.mkdir` applies the inherited umask. The function then changes
ownership to `root:smith` without setting the final directory mode. A
controller-side metadata-only reproduction confirmed mode `0700` under
that umask. This prevents the `smith` compiler from traversing the directory
even though staged registry files have mode `0440` and group `smith`.
The installed compiler successfully compiled the original controller-owned
registry in a separate read-only diagnostic. The failed helper's stderr was
not forwarded, and its temporary work directory was removed by the existing
cleanup path.

The correction sets the final request-directory mode to `0750` after
ownership changes. It grants group traversal without group write access or
access for other users. A regression test uses the real directory-creation
and registry-staging functions under umasks `022`, `077`, and `777`; it
checks directory modes, ownership requests, unchanged rollback-file mode
`0600`, and staged-file mode `0440`. Only ownership changes are mocked for
the unprivileged test runner. The test failed against the old code and
passes with the correction. Live account-boundary verification passed in
the resumed preflight under `umask 077` on 2026-09-06. The existing Ansible
wrapper installation carries the correction.

Review found the same missing final mode in all three Apply execution-receipt
directory writers. They now set mode `0750` after ownership changes so the
controller can read receipts under `umask 077`. A second regression test
reproduced the failure in each writer and checks directory mode `0750`, file
mode `0440`, ownership requests, and receipt content. Signature, nonce,
receipt-hash, rollback, and network behavior are unchanged.

The human authorized repair and resumption after the stopped attempt. Engine
`bd7d044678c9aa872c62a5d2b4f5f59219e8723f` passed the sealed Go build and tests,
all 499 Python tests, and the controller Ansible syntax check. All ten
installed controller components matched that engine. The human completed its
signed promotion and activation on 2026-09-06. The resumed preflight passed
with unchanged input hashes; DERP transport was informational. Keep that
engine fixed during the new attempt. Do not relax the caller's umask or
change network settings. Preserve the controller-private failure log,
baseline hashes, mode diagnostic, and Plan. Compiler error reporting still
needs a safe way to show the failed phase without private configuration.
The [current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns resumption.

# 2026-09-05 - DERP must not block one-box source adoption or rollback

The one-router workflow used `tailscale ping --c 1` with its default
direct-only completion condition, then rejected any DERP reply. This made
working relayed management access an Apply or rollback failure. The human
approved accepting either transport for this workflow. It now uses a bounded
relay-capable probe after authenticated router configuration verification.
Successful DERP checks produce a fixed informational notice in Ansible output,
the human approval terminal, and the controller audit log. Notices do not
change signed JSON, source records, or authorization checks.

Live signed rollback and re-adoption remain unverified and deferred. The
[current work queue](upstream-instance-target-architecture.md#current-work-queue)
owns the remaining development gates. The separate direct-IPv6 repair is
deferred and retains its strict success checks. Its discovery false refusal
below is not fixed by this change.

# 2026-09-05 - Overlay prerequisite rejects successful direct-path discovery

Read-only diagnosis reproduced the repair refusal in the actual router
playbook. Its `tailscale ping --c 3` command returned one DERP reply followed
by the required direct IPv6 UDP `41641` reply, with exit code 0 in about
1.5 seconds. The next assertion rejected the complete output because it
contained `via DERP`. An immediate repeat against the same fully qualified
peer name returned only the direct endpoint. A separate ten-packet sample
after discovery used that same direct endpoint for every reply. This is a false refusal of a
successful discovery sequence, not proof that the direct path is blocked or
that the Huawei pinhole needs another change.

The installed Tailscale 1.90.9 help and
[versioned client source](https://github.com/tailscale/tailscale/blob/v1.90.9/cmd/tailscale/cli/ping.go)
describe initial relay use during discovery and stopping when a direct path
is found. The repair currently treats discovery and final verification as
one test. Its helper also truncates the failure output to the play recap,
which hides the successful direct reply and the exact failed assertion.

Proposed correction: separate bounded discovery from a fresh direct-only
verification sample. Keep the final expected peer, global IPv6 endpoint,
UDP port, no-relay requirement, and signed evidence integrity checks. Do not
accept discovery success as the final proof, discard unexpected lines, add
unbounded retries, or reset Tailscale to make the check pass. Keep discovery
diagnostics distinct from the signed verification evidence. Review the
router prerequisite, ops post-apply verification, root evidence parser, and
their tests together. Test initial relay-to-direct discovery, relay-only
failure, return to relay during verification, changed endpoints, and unknown
output. Report the failed phase without exposing private configuration.

No implementation or network configuration changed during this diagnosis.
The result does not prove long-term path stability or complete the signed
repair, ops-to-peer verification, or box rollback acceptance.

# 2026-09-05 - Overlay signed revalidation includes changing runtime measurements

Review after the repeated collision-check failure found a separate blocker.
`overlay_revalidate` compares fresh router, ops, and Huawei evidence byte for
byte with the approved preimages. Saved live evidence contains finite IPv6
address lifetimes, firewall packet counters, and measured ping latency.
These measurements can change while configuration and the direct endpoint
remain unchanged. The earlier unsigned preflight did not test that interval.

The human approved a narrow runtime comparison. The root Apply program now
checks the archived bytes against their signed hashes, then compares fresh
configuration under the closed rules in
[ops-only overlay repair](upstream-instance-target-architecture.md#112-ops-only-overlay-ipv6-repair).
Tests cover a simulated review delay, increased counters, changed latency,
configuration drift, altered archives, and unknown evidence fields. Audit
records identify the comparison version and fresh evidence hashes without
copying configuration content.

Live acceptance is still pending. Promote the reviewed engine, install its
exact tools, and repeat preparation and signed revalidation before claiming
this blocker is resolved on the controller. Lifetime extensions and firewall
counter resets deliberately require fresh approval. If these occur during
normal review, record the evidence and review the rules; do not widen the
exceptions or remove the direct-path requirement as a workaround.

# 2026-09-05 - Repeated IPv6 preflight mistook its failed probe for a collision

The first successful repair preflight probed the proposed WAN link-local
next hop. With no reply, Linux retained a `FAILED` neighbour-cache entry
without a hardware address. The next preflight treated any matching cache
entry as an occupied address and refused before approval.

The check now permits only bare `FAILED` or `INCOMPLETE` unresolved entries.
It still refuses resolved neighbours, unknown entry shapes, and ping replies.
It checks the cache again after probing to detect a neighbour that resolves
but blocks echo replies. Address and neighbour inspection errors stop the
check. The signed operation must still pass kernel duplicate address
detection after adding the address. Do not flush the neighbour cache as a
workaround. Tests cover consecutive failed probes and real collisions.

# 2026-09-05 - Ops IPv6 helper must use the controller local connection

After the router snapshot handoff was corrected, the repair preflight failed
because the ops helper tried to SSH from the active controller to itself.
The same read-only snapshot succeeded with Ansible's local connection.
The helper now requires the local hostname to match the selected ops host
and sets the local connection, localhost address, and controller Python path
explicitly, as controller convergence does. Tests must reject a different
local host before invoking Ansible. This preflight failure did not change
network configuration.

# 2026-09-05 - Overlay snapshot output needs a separate writable directory

The signed repair preflight passed Freebox inspection but failed when Ansible
saved the router snapshot. Apply created its runtime directory as root with
mode `0750`, while the snapshot helper ran as `smith`. The helper could read
the directory but could not create its output there. A controller-local
snapshot in a private `smith` directory succeeded and confirmed the cause.

Apply now gives the helper a separate temporary output directory. It then
removes helper access and copies bounded regular files into root-owned,
read-only evidence before hashing them. Symlinks and hard links are refused.
Keep a regression test for the output account boundary, as well as the
existing tests for root-only rollback input handoff. The failed preflight
did not change the Freebox delegation or network configuration.

# 2026-08-31 - Freebox GET can omit read-only IPv6 metadata

The first overlay-repair preflight authenticated to the Freebox but stopped
because the IPv6 configuration result differed from the closed schema. The
official API defines `ipv6ll` as read-only, and its GET example omits that
field. The broker now accepts `ipv6ll` as optional and validates it when it is
present. A second preflight found `ipv6_firewall` and
`ipv6_prefix_firewall`. The installed Freebox user interface defines both
fields as Boolean checkboxes. The broker now accepts only the complete pair,
validates both values as Booleans, binds them into rollback evidence, and
refuses a configure or restore operation if either value changes. It still
reports only missing and unknown field names, never response values or
credentials. Do not make the delegation array, IPv6 enabled state, prefixes,
or next hops optional.

# 2026-08-31 - Huawei IPv6 pinhole can become stale after prefix changes

A changing residential IPv6 prefix is a plausible cause of a later direct-path
failure from `k002-router` to `k001-router`. The Huawei IPv6 virtual-host form
stores the complete `k001-router` global address. Its device selector appears
to fill that literal address; the form exposes no MAC, DHCPv6 DUID,
interface-ID, or prefix-independent host binding. A new rule with the current
`/128` produced a direct UDP `41641` endpoint. The prior address and endpoint
state were not recorded, so this result does not prove that prefix drift was
the sole cause of the earlier failure.

The diagnosis and safe manual recovery are in
`klokast-dev/runbooks/47-overlay-ipv6-direct-repair.md`. Do not broaden the
destination, disable the Huawei IPv6 firewall, or automate its administration
as a workaround. A future improvement needs authoritative documentation for
the exact Huawei firmware and a checked renumbering test. If no stable host
binding exists, design a narrow, audited, rollback-capable update boundary
before any automation is added.

# 2026-08-25 - Historical one-box direct-path and rollback input failures

The direct-only acceptance rule in this incident was replaced on 2026-09-05.
The current box source workflow accepts direct or DERP transport. Keep the
diagnosis and recovery evidence below; direct-path repair is deferred under
the [current work queue](upstream-instance-target-architecture.md#current-work-queue).

The first signed box-connectivity adoption did not reach Ansible because the
root Apply program passed a root-only rollback file to the `smith`-owned
compiler process. The adoption execution path was corrected to keep rollback
storage root-only and make exact, read-only copies in its short-lived execution
directory. A later authorized rollback found that its separate preflight path
still passed the root-only file directly. That path now uses the same staged
copy. Keep tests for each code path when a root action delegates a read-only
operation to `smith`.

During recovery inspection, one controller ping reached `k001-router` only
through the Hong Kong DERP relay. A later closed legacy verification
established a direct peer-to-peer path and passed all checks. If the relay-only
condition becomes frequent, endpoint and NAT state can be inspected in the
separate deferred repair work.

The condition occurred again during the unsigned adoption preflight. Both
peers reported working UDP and stable NAT mappings. The checked live router
verification also proved that the managed controller-side firewall allowed
the fixed Tailscale source port, STUN, established return traffic, and WAN
masquerade. Tailscale endpoint history then showed that the selected router
still used an obsolete public UDP mapping for the controller. The unprivileged
`tailscale debug restun` command failed closed because endpoint refresh needs
root. The human ran the command on both peers, and the direct path recovered
immediately. Any later endpoint-refresh diagnostic must be narrow, checked,
and audited, with only the active controller and one selected router in
scope. It must not grant general root access or change Tailnet policy. Do not
refresh endpoints to force a passing source acceptance result.

# 2026-08-25 - sealed builder cleanup depends on one Alpine mirror

Sealed build operations `0fdb18748cee` and `447ed5802533` compiled and cleaned
up their temporary guests, but the enclosing playbook refused each build when
the configured HUST Alpine mirror returned an I/O error during mandatory dom0
package reconciliation. A controller-dispatched probe confirmed that HUST
refused the connection while the official release-pinned Alpine HTTPS endpoint
worked. The public policy now uses the official endpoint. If that endpoint
also becomes unreliable, design a reviewed failover that does not weaken the
exact package policy or accept stale package sources silently.

# 2026-08-25 - Platform map refresh is slow when one VM is unreachable

The first box-connectivity observation refresh took several minutes because
`k001-iot` did not answer Tailscale SSH. The refresh correctly recorded the
VM as unreachable and completed, and Plan v3 kept the unrelated finding out
of the one-router action. Add bounded per-host connection timeouts or reuse a
fresh prior non-target VM observation so one offline VM cannot delay a narrow
router source change.

A later dom0-only refresh and one-router check also took several minutes while
`k001-dom0` and `k001-router` remained reachable. Each Ansible task opened a
new connection across the household link. Investigate safe connection reuse
or a narrow, bounded remote verifier so read-only acceptance does not incur
the connection delay once for every task.

# 2026-08-25 - failed Apply preflights need a retention policy

The first Authority State v1 conversion attempt stopped after signature
verification because nonce evidence incorrectly required a Plan hash. The
corrected Apply program binds a conversion nonce to its Authority State hash.
The failed attempt left an immutable preflight directory and an empty,
consumed nonce file. Keep both as failure evidence. Define a root-owned
retention and archival policy before these evidence directories need cleanup.

# 2026-08-25 - Go tests run only in the sealed builder

The infra-agent can run the Python and shell checks, but it has no Go or
Ansible executable. The active controller has Ansible but also has no Go
executable. The canonical engine build runs the complete vendored Go tests in
its pinned builder image. Run Ansible syntax checks on the controller with the
repository-local configuration and role path. Do not install extra build tools
on the infra-agent or controller only to duplicate these checks.

# 2026-08-22 - GitHub App cannot remove its last selected repository

GitHub rejects removal when a repository is the last selected repository of
an App installation. Do not change the temporary private-instance App to
**All repositories**, and do not create an unrelated carrier repository. For
a single-repository organization, uninstall the organization App installation
but keep the App definition and credentials until the signed controller action
proves that the installation ID no longer exists. The action must accept only
an exact not-found result, require the dedicated App to have no other
installation, verify the read-only deploy key, and then delete the controller
credential.

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

The Platform should support these standardized connection modes:

- Tailscale, for example, for inbound requests from family users;
- Cloudflare, for example, for inbound static website requests from external
  users;
- a local access point, for example, for OS package downloads or traffic with
  torrent peers;
- a local VPN client, for example, for an airunner;
- a LAN, for example, for a user who plays music;
- Tor, for example, for Bitcoin Core.

Define how each mode uses router zones and how an app requests the mode. The
design must make later connection modes easy to add.

# 2026-08-22 - migrate this `todo.md` to GitHub "issues" and feature requests

Or to another ticketing system, to industrialize the monitoring and resolution
of tickets.

# 2026-08-21 - MacBook bootstrap wrappers require a real macOS integration run

The infra-agent host cannot run the interactive private-instance wrappers
because they require macOS, Tailscale access from the trusted MacBook, and
Apple CryptoTokenKit. Deterministic tests cover the separate controller and
approved-engine pins. The active controller can verify the sealed build and
the installed root wrapper. The human must still rerun
`prepare-private-instance-bootstrap` on the trusted MacBook to verify the
complete terminal and Touch ID path after a wrapper change.
More generally, the end-to-end User Experience of the various Klokast processes
must be mapped, reviewed, and improved. For example, review the authentication
process on the trusted MacBook. Avoid unnecessary fingerprint scans, and make
the signed intent clear to the user.

# 2026-08-16 - make Touch ID signing prompts more explicit

During the procedure in
[Private Instance Bootstrap](../klokast-dev/runbooks/40-private-instance-bootstrap.md),
the current Touch ID prompt identifies `ctcardtoken` and does not show the
intent. The prompt should identify Klokast and make the signed intent clear.
The standard Touch ID prompt cannot be customized. One proposal is a signed,
one-shot Klokast Approval app. It would validate
the intent, display it, compute its digest, and sign the same in-memory bytes.
Apple’s native authentication API supports an application name and a clear
`localizedReason`. Apple documents this behavior
(https://developer.apple.com/documentation/localauthentication/lacontext/localizedreason).
The Security framework can also associate an LAContext with keychain
authentication. Apple documentation
(https://developer.apple.com/documentation/security/ksecuseauthenticationcontext).
This change will probably require a signer migration or a new signature format
because the current OpenSSH security-key format contains more flags and counter
fields.

# 2026-08-09 - NanoKVM Tailscale Serve is runbook-managed

The NanoKVM HTTPS listener presents a self-signed certificate with
the certificate Common Name `localhost`. Tailscale connectivity and the operator grant can both work
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

# 2026-08-23 - restore the app store routing document

The root agent instructions require `apps/STORE.md` before app work, but that
file does not exist. `apps/README.md` contains the current supported-app list
and routing rules. Create `apps/STORE.md` or change the root instruction to
name the existing authority. Do not keep two app catalogs with different
content.

# 2026-08-09 - validate dom0 dependencies against the boot repository

The first boxa dom0 policy canary showed that the live Alpine 3.23 repository
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

# 2026-08-09 - boxa ops controller cannot reliably reach GitHub

During the exact APK-world rollout, `boxa-ops` repeatedly failed to fetch
`https://github.com/klokast/klokast-box.git`; one controller convergence ended
after 135 seconds with curl error 28. Its clean checkout had to be fast-forwarded
from an exact Git bundle created on active controller `boxb-ops`. The package
policy had already converged successfully, but the later public-checkout rehome
task could not finish, and the ops verification upstream probe is expected to
fail for the same reason. Investigate `boxa-ops` DNS, outbound routing, and
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

## Instance-only verification acceptance

The dependency audit found that normal inventory policy links still entered
the retained inventory tree, and `platform-check` skipped registry checks when
the old YAML file was absent. The instance-only change removes these two
dependencies. Keep the memory-cache regression check: observed Ansible facts
must not enter desired-state comparisons, but declared variables must remain.

The isolated consumer comparison uses sealed projection replies at the source
broker boundary. It does not prove installed broker behavior or live signed
execution. Complete the installed unsigned and signed checks in
[the instance-only runbook](../klokast-dev/runbooks/53-instance-only-verification.md)
after human engine promotion. Preserve the retained source history, adoption
archives, receipts, and legacy recovery inputs. The work queue remains in
[the target architecture](upstream-instance-target-architecture.md#current-work-queue).

The first instance-only sealed test run rejected a test receipt written with
Go struct-field order instead of canonical stored JSON. Keep test evidence
canonical through `canonicalTestJSON`; the production verifier must continue
to reject non-canonical stored receipts. The isolated consumer views must also
reuse one temporary path so public manifest path provenance stays equal.
