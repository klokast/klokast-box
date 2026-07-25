The directory `apps/bootstrap-iso-debian` contains the tooling to turn a blank mini PC into a disposable Debian control environment in RAM. This Debian system is then reachable by the deployment server via Tailscale and Ansible, to continue the installation flow through Ansible playbooks.

# Context resolver

`apps/bootstrap-iso-debian/` directory content:

- `build-bootstrap-iso.sh`  outputs the bootable `bootstrap ISO`, which is the image that must be brought to the new mini PC, via USB drive or remote KVM, so that the mini PC can boot from it and join the Platform.
- `build-alpine-seed.sh`    outputs the `alpine-standard-${release}-${arch}.tar.gz` tarball, that contains the files required by Ansible Playbook 12 to boot Alpine in diskless mode: `vmlinuz-lts`, `initramfs-lts`, `modloop-lts`, and `apks/<arch>/`
- `portal/`                 contains the Go onboarding portal. The Podman builder container compiles it and the bootstrap ISO includes only the compiled binary.

- `feed-bootstrap-iso.md`   explains how to use the "bootstrap ISO" output
- `feed-alpine-seed.md`     explains how to use the Alpine seed tarball output
- `build-bootstrap-iso.md`  explains how to run `build-bootstrap-iso.sh`
- `build-alpine-seed.md`    explains how to run `build-alpine-seed.sh`
- `builder-container.md`    explains how, inside `yii-bak`, to prepare the `bootstrap-live-builder` container that runs the bootstrap ISO builder workflow
- `coding-instructions.md`  states the required outputs, the conditions for success, and the deployment recommendations. Read this if you are building or deploying the `apps/bootstrap-iso-debian/` application.
- `runbook-access-artefacts.md` explains how the user can access the output files and container.
- `release-artifacts.md` explains how to publish verified bootstrap artifacts as private GitHub Release assets.
