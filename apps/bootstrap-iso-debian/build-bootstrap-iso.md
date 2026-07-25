This document describes `apps/bootstrap-iso-debian/build-bootstrap-iso.sh`: the builder script for the bootstrap ISO.

The builder produces two artifacts:
  1. bootstrap ISO, based on Debian Live
  2. Alpine diskless seed tarball later consumed by `12-bootstrap-diskless-build.yml`

The builder runs in the Podman VM `yii-bak`. It runs from the
`bootstrap-live-builder` container managed by Ansible, and it runs
rootful/privileged because Debian `live-build` needs mount-capable chroot
stages.


It is not part of the chain of playbooks that builds dom0 and the VMs. The builder is just an application running on the Platform. It could actually run on the development server or the developer laptop.



## Workload Layout

The Ansible playbook is `ansible/playbooks/82-bootstrap-iso-debian-builder.yml`
This playbook calls the role `ansible/roles/bootstrap-live-builder/`

The workload creates these host-side paths on `yii-bak`:

- `/etc/klokast/bootstrap-live-builder/`
- `/srv/bootstrap-live-builder/src/klokast/`
- `/srv/bootstrap-live-builder/output/`
- `/srv/bootstrap-live-builder/work/`
- `/srv/bootstrap-live-builder/home/`

## Security Model

The builder container carries its own Tailscale identity only for direct
artifact upload to the NanoKVM. Operator access still reaches it through
`yii-bak`.

Unlike steady-state service containers, this builder is a deliberate exception to the rootless Podman rule:

- the repo checkout, output path, and SSH material stay on `yii-bak` under the normal backend admin workspace
- the actual Debian live-build container runs privileged/rootful
- reason: `live-build` and `debootstrap` need mount-capable chroot stages, and the rootless container path fails at that boundary

The upload path:

- build inside the `bootstrap-live-builder` container on `yii-bak`
- enroll that builder container as a Tailscale `tag:back` identity through the
  existing `/usr/local/sbin/ts-authkey-back` wrapper
- keep public DNS resolvers on the builder container for Debian mirror access;
  the container resolves the NanoKVM target dynamically with `tailscale ip`
- upload from the builder container to the remote KVM device
  `root@oob.<tailnet>.ts.net:/data/` over Tailscale
- construct `<tailnet>.ts.net` from `tailscale status --json`; do not hardcode
  the Tailscale IP address
- keep a dedicated SSH upload key for that path

One auth key still matters, but it is entered only in the booted portal:

- Tailscale tag of the Debian-based bootstrap live system: `tag:bootstrap`
- Tailscale-SSH user: `root`
- transport: pasted into the portal at `http://kk.local/` or
  `http://klokast.local/`
- command: `tailscale up --ssh --hostname=<node>-bootstrap
  --advertise-tags=tag:bootstrap --auth-key=<key>`
- duplicate names: if Tailscale assigns a suffixed bootstrap machine name, the
  portal asks for another box name without asking for the auth key again; it
  also rejects names that match visible machines from `tailscale status --json`
- storage: never in inventory, never in committed files, never in the generic
  Debian live ISO, never in a companion config ISO

## Intended Operator Flow

1. Converge the builder workload:

```sh
ansible/bin/bootstrap-live-iso build-builder --resources-registry path/to/platform-resources.yml
```

2. Build the ISO inside the `yii-bak` Podman builder container:

```sh
ansible/bin/bootstrap-live-iso build-iso
```

The runner builds a generic Debian live ISO that includes the compiled Go
onboarding portal. The node name and `tskey-auth-*` are not build inputs. They
are entered only after the mini PC has booted the ISO and published
`kk.local`/`klokast.local` on the DHCP LAN.

3. Transfer the ISO and SHA512 checksum to the NanoKVM, then stop and remove the builder:

```sh
ansible/bin/bootstrap-live-iso transfer-iso
ansible/bin/bootstrap-live-iso stop-builder
ansible/bin/bootstrap-live-iso remove-builder
```

The one-command path is:

```sh
ansible/bin/bootstrap-live-iso all --resources-registry path/to/platform-resources.yml
```

The runner writes artifacts on `yii-bak` under `/srv/bootstrap-live-builder/output/`
and transfers the ISO directly from the builder container to
`root@oob.<tailnet>.ts.net:/data/`.

## Tests

The Ansible role is expected to verify:

- Podman is present on `yii-bak`
- the repo checkout is synchronized onto the backend VM
- the builder image is built locally
- the builder container is running
- the Debian toolchain is present inside the container
- the builder container can upload a test artifact to
  `root@oob.<tailnet>.ts.net:/data/` over Tailscale

## Success

Success for this workload means:

1. `ansible/playbooks/82-bootstrap-iso-debian-builder.yml` converges on
   `yii-bak` without inventing a separate Tailscale or container-management
   identity.
2. The generated helper can build both:
   - a Debian live stage-1 ISO
   - an Alpine diskless seed tarball
3. The final artifacts land under `/srv/bootstrap-live-builder/output/` and
   are uploaded from the builder container to `root@oob.<tailnet>.ts.net:/data/`.
4. No auth key or controller secret is stored in inventory or committed files.

## Relationship To The Older Alpine Builder

The older Alpine `mkimage` bootstrap builder has been removed. The
`bootstrap-live-builder` is the only maintained stage-1 builder because it keeps:

- stage 1 as a small Debian live carrier
- stage 2 as the existing Alpine dom0 install flow
