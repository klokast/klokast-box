# Klokast Instance Specification v1

## Purpose

The Klokast Instance Specification defines the private desired state of one
Klokast installation. Version 1 is not released. This repository can change
version 1 while the first deployment test is in progress.

The specification separates these authorities:

```text
effective desired state
    = one approved klokast-box commit
    + one private klokast-instance.json

runtime authority
    = effective desired state
    + controller-held secrets
```

The public `klokast-box` repository owns implementation, schemas, app
manifests, connectivity capabilities, automation, and neutral tests. The private instance
repository owns deployment intent and the engine lock. It must not contain
secrets, generated state, live status, or user data.

This document is the normative source for the JSON contract and `klokast` CLI
behavior. [Secret Authority](secret-authority.md) owns signed execution rules.
The completed transition narrative is available in Git at commit `186cfa9`.

## Repository layout

A private instance repository has two authoritative files:

```text
klokast-instance.json
klokast.lock.json
```

It also has `.gitignore`, `AGENTS.md`, and `README.md`. These three files are
support files. The repository has no private app manifests, extensions,
inventory, generated-state directory, or site executor.

The old unreleased files `klokast.yml`, `klokast.lock.yml`,
`ops/deployment.yml`, and `ops/platform-resources.yml` are invalid instance
inputs.

## JSON rules

Version 1 uses JSON because automation will write the files. JSON has one
standard data model and does not have YAML tags, anchors, or implicit scalar
types. A JSON parser still cannot explain every editing mistake well, so the
checker rejects duplicate keys and unknown fields and reports a JSON path.

Property names in the private files use kebab case. Object keys use stable,
lowercase IDs. Unordered sets are JSON arrays. The checker requires unique
items. The resolver sorts sets before it creates a projection. The `airunners`
array is an ordered preference list. The resolver does not sort it.

Each file has a `$schema` URL. The URL uses the full approved engine commit:

```text
https://raw.githubusercontent.com/klokast/klokast-box/<commit>/schemas/klokast-instance-v1.schema.json
https://raw.githubusercontent.com/klokast/klokast-box/<commit>/schemas/klokast-lock-v1.schema.json
```

The commit pin makes editor validation use the same schema as the sealed
binary. The files keep `"schema-version": 1`. A file-format change during the
unreleased test does not make the version 2 specification.

## Instance document

This example shows a neutral two-box shape. Every identity and location value
is an example. The human must replace it with private desired state before
publication.

```json
{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/<commit>/schemas/klokast-instance-v1.schema.json",
  "schema-version": 1,
  "tailscale": {
    "tailnet-dns-name": "<private-tailnet-dns-name>",
    "members": {
      "<private-login>": {
        "roles": ["operator", "family"]
      }
    }
  },
  "boxes": {
    "boxa": {
      "site": "site-a",
      "country": "XA",
      "description": "",
      "connectivity": ["local-ap-uplink", "direct-wan-egress", "overlay"]
    },
    "boxb": {
      "site": "site-b",
      "country": "XB",
      "description": "",
      "connectivity": ["overlay"]
    }
  },
  "controllers": {
    "active": "boxb",
    "standby": "boxa"
  },
  "airunners": ["boxb-ops-airunner"],
  "apps": {
    "music": {
      "desired-state": "absent",
      "data": {
        "library": {
          "box": "boxb",
          "retention": "preserve"
        }
      }
    }
  }
}
```

Platform time is always `Etc/UTC`. The instance document has no timezone
field.

The document has no `instance` object or instance ID. The private Git
repository identity and controller source receipt already identify the source.

`tailnet-dns-name` uses the Tailscale name for the Tailnet DNS suffix. Each
member has one or more roles. At least one member must have both `operator`
and `family` roles.

## Boxes, sites, and runtime names

A box declares its site metadata directly. `site` is a stable private label,
`country` is a two-letter country code, and `description` can be empty. There
is no top-level site catalog. If more than one box uses the same site label,
each box must use the same country and description for that site.

A box ID is also its runtime prefix. For example, `boxb` derives these names:

```text
boxb-dom0
boxb-router
boxb-bak
boxb-dmz
boxb-iot
boxb-ops
boxb-ops-airunner
```

This rule removes the old translated runtime prefix. Box IDs cannot use
a reserved runtime suffix or produce a DNS label longer than 63 characters.

The controller object selects one active box and, optionally, one different
standby box. It does not contain live controller status.

`airunners` is a non-empty, duplicate-free array of exact runtime identities.
The first item has the highest preference. Every item remains desired and must
be online. Priority does not start, stop, select, or fail over a runner.

An airunner identity has one of two forms:

- `<box>-ops-airunner` is a container in an active or standby `<box>-ops` VM.
  It must have `tag:airunner`.
- `<cloud>-ops` is a cloud VM. Its system hostname and Tailscale machine name
  must both equal the array item. It must have `tag:infra`.

The public [cloud provider catalog](../cloud-providers.json) defines supported
`<cloud>` IDs. A box ID cannot equal a cloud-provider ID. The checker rejects
unknown providers, other suffixes, name collisions, duplicate items, and the
old placement object.

## Connectivity capabilities

The `connectivity` array is a unique set of available and enabled box
capabilities. It does not select an application flow or a box-wide access
policy. Version 1 accepts only these values:

- `overlay`: the box has the selected overlay transport.
- `local-ap-uplink`: the box has a local access-point uplink.
- `direct-wan-egress`: the box can use direct WAN egress.
- `edge-tunnel-ingress`: the box can use an outbound edge tunnel for ingress.
- `direct-wan-ingress`: the box can receive direct WAN ingress.

Every version 1 box must include `overlay`. The top-level `tailscale` object
selects Tailscale as the only supported version 1 overlay provider. Nebula and
multiple overlay providers are deferred.

The compatibility adapter maps the five values to `overlay`, `ap-uplink`,
`direct-egress`, `edge-ingress`, and `direct-ingress`. It derives prohibited
capabilities as the exact complement across the supported legacy capability
vocabulary. The legacy box-wide `policy` field is not accepted.

## Application and data lifecycle

The `apps` object contains only declared application intent. It is not an app
store and does not list every app that Klokast supports.

An app binding has `"desired-state": "present"` or
`"desired-state": "absent"`.

A present app must have `placement`. It can also have typed `features` and
named `data`. Placement has one of these shapes:

```json
{"mode": "single-box", "box": "boxa"}
{"mode": "multi-box", "boxes": ["boxa", "boxb"]}
{"mode": "active-passive", "active": "boxb", "passive": "boxa"}
```

`single-box` selects exactly one runtime box. `multi-box` selects a set of
independent placement boxes. It does not define a master. `active-passive`
selects one active box and one different passive box.

The public app manifest defines the supported placement mode, feature names,
feature types, and data IDs. Version 1 features are Boolean values or values
from a manifest-defined string enumeration.

For example, Nextcloud can select optional public ingress with:

```json
"features": {"public-ingress": "cloudflare-tunnel"}
```

Omission means that optional public ingress is disabled. The manifest binds
this value to the `cloudflare-tunnel-egress` resource. The resource requires
`edge-tunnel-ingress` on every placement box.

Data belongs to its app. It is not a top-level catalog. A data entry names one
manifest-defined logical dataset, one box, and `"retention": "preserve"`.
For Music, the logical `library` dataset includes the physical
`klokast-music-library` and `klokast-music-playlists` volumes. Reconstructable
MPD, myMPD, runtime, and Tailscale state are not part of this dataset.

When an app is removed but its declared data stays, keep the app entry, set
`desired-state` to `absent`, remove `placement` and `features`, and keep a
non-empty `data` object. When the declared data is also removed, remove the
whole app entry. Omission means no app presence intent. Omission alone never
authorizes deletion of unknown or undeclared storage.

The instance records desired state, not observed state. Do not add `running`,
`stopped`, health, container, VM, or service-status fields.

## Shared VM update intent

The optional `vm-updates` object declares standing update intent. Omission
disables automatic replacement. This is a closed input contract:

```json
"vm-updates": {
  "enabled": true,
  "targets": {"boxa": ["bak", "dmz", "iot"]},
  "exclusions": [],
  "branch-policy": "tested-stable",
  "maintenance-window": {"start": "02:00", "end": "04:00", "last-start": "03:00"},
  "canary-hours": 24,
  "replacement-minutes": 30,
  "recovery-minutes": 30
}
```

Each target must name a declared box and one or more shared roles. Router,
controller, dedicated app VM, Debian, and Ubuntu replacement are outside this
contract. A durable exclusion has `box`, `role`, and a non-empty `reason`.
It must refer to a declared target. Each target can have only one exclusion.
The canary period can be 24 to 168 hours. Other timing values are fixed in
this release. All times are UTC. One installation can replace only one VM at
a time. Necessary recovery can continue after the maintenance window closes.

The policy changes the desired-state projection hash. Target and exclusion
ordering does not change that hash. It does not change the engine lock.
Enabling this field alone does not authorize execution. The activation rules
are in [Secret Authority](secret-authority.md#standing-vm-update-authority).
See [VM updates](platform-updates.md) for implementation status and commands.

Source recipes, package lists, tests, and maintenance adapters belong to the
approved public engine. Logical data retention stays in `apps.<app>.data`.
Physical LV and mount mappings, package manifests, exact active and previous
release assignments, build receipts, inventory, and operation journals are
generated controller records under `/var/lib/klokast`. Rebuildable downloads
use `/var/cache/klokast`. Persistent artifact storage holds disks and matching
kernel and initramfs files by checksum. Secret stores and retained data volumes
remain separate. None of these outputs belongs in the private repository.

An accepted operation also needs a narrow persistent copy on its box. That
copy permits offline boot and recovery of that exact operation. It cannot
select a new release. Large artifacts and journals use persistent LVM-backed
storage outside `.apkovl`; only small boot configuration uses dom0 persistence.
Generated machine configuration must identify its source, profile, and accepted
release. A local edit is drift. Discovery cannot promote it to desired state.

Automatic package selection under an activated policy must not write Git.
`klokast.lock.json` remains an engine lock. Normal provisioning and
reconciliation must consume the same accepted release assignment before they
can manage an adopted guest. They must not restore obsolete repositories,
packages, Xen boot paths, or kernels.

## Engine lock

`klokast.lock.json` has this shape:

```json
{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/<commit>/schemas/klokast-lock-v1.schema.json",
  "schema-version": 1,
  "engine": {
    "repository": "https://github.com/klokast/klokast-box",
    "ref": "main",
    "commit": "0123456789abcdef0123456789abcdef01234567"
  }
}
```

The full commit is authoritative. The sealed binary checks all three engine
values. A human reviews the lock but does not edit it.

## Initialization and checking

The implemented offline commands are:

```text
klokast init --instance PATH --values FILE [--json]
klokast check --instance PATH [--json]
klokast plan --instance-only --instance PATH --observation FILE --instance-source-receipt FILE --authority-state FILE --controller-toolchain-receipt FILE [--json]
klokast plan --legacy-retirement --retirement-phase verify --retirement-evidence FILE --instance PATH --observation FILE --instance-source-receipt FILE --authority-state FILE --controller-toolchain-receipt FILE [--json]
klokast plan --instance PATH --compatibility-deployment FILE --compatibility-registry FILE --compatibility-controller-ha FILE --observation FILE --instance-source-receipt FILE --authority-state FILE --controller-toolchain-receipt FILE [--json]
klokast doctor --instance PATH --observation FILE [--json]
```

The values file for `init` is the complete `klokast-instance.json` document.
`init` copies the support template, writes deterministic JSON and the exact
lock, creates a standalone Git repository on `main`, and stages the files. It
does not make a commit, add a remote, use the network, or copy the values file
into the new repository under another name. It validates the staged result
before an atomic no-replace publication.

`check` is read-only. It requires a standalone Git repository and tracked
authoritative inputs. It rejects symlinks, unsafe paths, duplicate JSON keys,
unknown fields, old YAML inputs, secret-like values, invalid references,
unsupported apps, wrong app placement modes, unknown features or data, and an
engine or schema commit mismatch. It accepts a dirty worktree so a human can
check edits before commit.

For the initial publication and later desired-state updates, the human edits
only `klokast-instance.json` on the trusted MacBook and stages that file. The
human does not edit `klokast.lock.json` or the support files. The MacBook
publication helper sends only the edited instance document through standard
input to the active controller. Before the first commit, the controller uses
the owner-only unborn seed and its bootstrap engine. After a deployment
checkout exists, it uses that exact clean commit, a fresh source receipt, and
the engine selected by the lock and its immutable activation receipt. It
checks the candidate with the sealed binary, requires complete Authority State
v5 with all setting groups owned by Instance Specification v1, and refuses any
restored legacy input path. It also checks the rollback form with the recorded
previous sealed engine. It returns the checked Git tree and removes the
temporary copy. The helper commits only when that tree equals the staged
MacBook tree.
For a later update, it also requires MacBook, GitHub, source receipt, and
controller `main` to have the same base commit and tree.
`publish-private-instance --check` performs the same check without a commit,
push, or publication. The helper does not merge or overwrite a changed remote
branch.

The human changes the selected engine only through
`promote-private-instance-engine`. A metadata-only promotion changes the two
`$schema` commits and `engine.commit`. The structural legacy Instance v1
transition converts `tailnet` to `tailscale`, converts each box
`connectivity-profiles` field to `connectivity`, moves referenced site
metadata into each box, and removes the redundant instance ID and site map.
The connectivity transition converts `tailscale` to `overlay` and converts
`local-ap-direct-egress` to the adjacent `local-ap-uplink` and
`direct-wan-egress` pair. Its inverse rejects partial pairs and all
non-invertible capabilities.
The controller independently reconstructs this closed transform and its exact
inverse. Promotion uses a canonical `klokast/klokast-box` `main` descendant,
exact sealed old and new builds, one short-lived Touch ID approval, a forward
private commit, and immutable promotion and activation receipts. `--rollback`
selects only the previous engine and recorded inverse schema transition from
the active activation receipt and also creates a forward commit.

## Projection, compatibility, and observation

The resolver is deterministic and offline. It derives runtime names, UTC,
legacy Tailnet groups, connectivity capabilities, controller placement,
airunner identities, app placement, features, and retained data. It sorts maps
and sets before it creates the projection hash. It preserves the `airunners`
order, and a priority change changes the projection hash.

Plan v5 emits `control_plane.airunners` as the same ordered string array. It
does not emit airunner kinds, placement fields, or derived airunner objects.

The compatibility planner compares this projection with the current private
deployment file, platform-resource registry, and controller registry. A
finding is `matched`, `derived`, `compatibility_only`, `conflict`, or
`unsupported`. A disabled legacy app that is omitted from `apps` resolves to
absent. An enabled legacy app must have explicit present intent.

With `--instance-only`, fresh Observation v1 and Instance Source Receipt v1,
complete Authority State v5, and Controller Toolchain v7 or v8, `plan` emits
the closed Plan v8 contract. Use the current exact v8 receipt for a new
operation. Toolchain v7 support reads historical verification and recovery
artifacts.
With `--legacy-retirement --retirement-phase verify`, immutable retirement
evidence, and Controller Toolchain v8, it emits the current Plan v9
retired-state verification contract. Both require all six setting groups and
their exact instance scopes. They reject compatibility inputs and migration
targets and emit verification-only source actions.

Explicit compatibility and migration planning retains the prior interfaces
only for recovery, tests, and reading historical artifacts,
including `--connectivity-target non-controller|active-controller` and
`--migration-target connectivity|controller-identity|registry|inventory`.
Their defaults are `non-controller` and `connectivity`. Historical Plans retain
their original contracts. Normal operation must not use these interfaces as a
fallback source of authority. Planning applies no changes, and Plan v1 cannot
authorize execution. `doctor` uses the same
projection and checks only the declared standard substrate. Extra legacy
resources do not become desired state. `doctor` checks every listed airunner
for presence, online state, and its required tag. It does not select a runner,
implement failover, or check a separate airunner Xen guest. The router and
controller guests must be online, configured, running, and set to start
automatically. The shared `bak`, `dmz`, and `iot` guests must keep their
Tailnet identity and tag and their Xen configuration, but they can be stopped,
offline, and without autostart. Doctor does not infer shared-guest runtime
intent from a compatibility-only registry.

Generated and observed data is output. It is never another Git authority.

## Authority and bootstrap boundary

The human authors and pushes the private repository from a trusted
workstation. The active controller has a root-held read-only deploy key and a
push-disabled checkout. Airunners can work on the public implementation
repository, but they do not receive the private repository or controller
secrets.

Deployable `klokast` binaries come from the active controller's sealed,
networkless builder. The private bootstrap uses a short-lived GitHub App only
to register the empty repository and its read-only deploy key. The App does not
push content. The human removes its repository access after the first push.

The active controller stores secrets in `/etc/klokast`, generated state and
evidence in `/var/lib/klokast`, and rebuildable artifacts in
`/var/cache/klokast`. Application storage contains persistent user data.

## Deferred work

Later specification versions can add more connectivity providers, app feature
types, data operations, and site executors. Version 1 does not give an app or
an airunner authority to grant itself resources or delete undeclared data.
