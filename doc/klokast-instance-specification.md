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
manifests, connectivity profiles, automation, and tests. The private instance
repository owns deployment intent and the engine lock. It must not contain
secrets, generated state, live status, or user data.

The [upstream/instance target architecture](upstream-instance-target-architecture.md)
records implementation status, transition Plan semantics, engine promotion,
future authorized apply, migration, and legacy-removal gates. This document
remains the normative source for the JSON contract and `klokast` CLI behavior.

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

This example shows the first two-box deployment shape. Private login and
Tailnet values are placeholders.

```json
{
  "$schema": "https://raw.githubusercontent.com/klokast/klokast-box/<commit>/schemas/klokast-instance-v1.schema.json",
  "schema-version": 1,
  "instance": {
    "id": "klokast-instance"
  },
  "tailnet": {
    "tailnet-dns-name": "<private-tailnet-dns-name>",
    "members": {
      "<private-login>": {
        "roles": ["operator", "family"]
      }
    }
  },
  "sites": {
    "milla": {
      "country": "FR",
      "description": ""
    },
    "mingdu": {
      "country": "CN",
      "description": ""
    }
  },
  "boxes": {
    "k001": {
      "site": "milla",
      "connectivity-profiles": ["local-ap-direct-egress", "tailscale"]
    },
    "k002": {
      "site": "mingdu",
      "connectivity-profiles": ["tailscale"]
    }
  },
  "controllers": {
    "active": "k002",
    "standby": "k001"
  },
  "airunners": ["k002-ops-airunner"],
  "apps": {
    "music": {
      "desired-state": "absent",
      "data": {
        "library": {
          "box": "k002",
          "retention": "preserve"
        }
      }
    }
  }
}
```

Platform time is always `Etc/UTC`. The instance document has no timezone
field.

`tailnet-dns-name` uses the Tailscale name for the Tailnet DNS suffix. Each
member has one or more roles. At least one member must have both `operator`
and `family` roles.

## Sites, boxes, and runtime names

A site ID is a stable private label. A site has a two-letter country code and
a description. The description can be empty.

A box ID is also its runtime prefix. For example, `k002` derives these names:

```text
k002-dom0
k002-router
k002-bak
k002-dmz
k002-iot
k002-ops
k002-ops-airunner
```

This rule removes the old `box-002` to `k002` translation. Box IDs cannot use
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

## Connectivity profiles

The instance selects profiles. It does not repeat low-level developed
capabilities or access policy. Version 1 has these profiles:

- `tailscale`: private ingress, upload, and control use the Tailscale overlay.
  Household WAN egress and public ingress are not enabled.
- `local-ap-direct-egress`: the box has a local access-point uplink and can
  send household traffic through direct WAN egress. Public ingress and
  residential-gateway LAN ingress are not enabled.

Profiles are a set. `k001` uses both profiles. `k002` uses only `tailscale`.
Every version 1 box must select `tailscale`. This profile supplies the current
private control path. Another overlay provider requires a later specification
change before it can replace this profile.
The public engine resolves profiles to current capability and policy fields
when it compares the instance with the legacy platform-resource registry.

The name `tailscale` identifies the current provider. A later version can add
another profile for Nebula, Cloudflare, or another overlay implementation.

## Application and data lifecycle

The `apps` object contains only declared application intent. It is not an app
store and does not list every app that Klokast supports.

An app binding has `"desired-state": "present"` or
`"desired-state": "absent"`.

A present app must have `placement`. It can also have typed `features` and
named `data`. Placement has one of these shapes:

```json
{"mode": "single-box", "box": "k001"}
{"mode": "multi-box", "boxes": ["k001", "k002"]}
{"mode": "active-passive", "active": "k002", "passive": "k001"}
```

`single-box` selects exactly one runtime box. `multi-box` selects a set of
independent placement boxes. It does not define a master. `active-passive`
selects one active box and one different passive box.

The public app manifest defines the supported placement mode, feature names,
feature types, and data IDs. Version 1 features are Boolean values or values
from a manifest-defined string enumeration.

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
klokast plan --instance PATH --compatibility-deployment FILE --compatibility-registry FILE --compatibility-controller-ha FILE [--observation FILE --instance-source-receipt FILE] [--json]
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
input to the active controller. The controller creates a temporary owner-only
copy of the seed, checks the candidate with the pinned sealed binary, returns
the checked Git tree, and removes the temporary copy. The helper commits only
when that tree equals the staged MacBook tree. For a later update, it also
requires GitHub `main` to equal the local commit on which the edit is based.
It does not merge or overwrite a changed remote branch.

## Projection, compatibility, and observation

The resolver is deterministic and offline. It derives runtime names, UTC,
legacy Tailnet groups, connectivity capabilities, controller placement,
airunner identities, app placement, features, and retained data. It sorts maps
and sets before it creates the projection hash. It preserves the `airunners`
order, and a priority change changes the projection hash.

Plan v1 emits `control_plane.airunners` as the same ordered string array. It
does not emit airunner kinds, placement fields, or derived airunner objects.

The compatibility planner compares this projection with the current private
deployment file, platform-resource registry, and controller registry. A
finding is `matched`, `derived`, `compatibility_only`, `conflict`, or
`unsupported`. A disabled legacy app that is omitted from `apps` resolves to
absent. An enabled legacy app must have explicit present intent.

With a fresh Observation v1 file and Instance Source Receipt v1, `plan` emits
a hashed Plan v1 artifact. It does not apply changes. `doctor` uses the same
projection and checks only the declared standard substrate. Extra legacy
resources do not become desired state. `doctor` checks every listed airunner
for presence, online state, and its required tag. It does not select a runner,
implement failover, or check a separate airunner Xen guest.

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

The [upstream/instance target architecture](upstream-instance-target-architecture.md)
owns the ordered design work for engine promotion, Plan hardening, authorized
apply, migration, and legacy removal. Later specification versions can add
more connectivity profiles, app feature types, data operations, and site
executors. Version 1 does not give an app or an airunner authority to grant
itself resources or delete undeclared data.
