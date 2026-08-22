# Deployment recommendations

Deploy the `apps/bootstrap-iso-debian/` application as :

- one container only
- without backup container and redundancy across sites
- one site only
because this application is only used during a short time (to enroll a new box into the Platform), and it can always be restarted or rebuilt quickly and easily if it fails.

- on a box near the location of the new box (short tailscale-ssh latencies),
because the output files are big, and can take time to transfer between boxes.

The maintained build location is the `bootstrap-live-builder` Podman container
on `yii-bak`; the deployment server may orchestrate Ansible and SSH commands,
but it does not build the ISO.

# Security hardening

The Debian live builder is an explicit privileged exception, not a normal
steady-state app. Before converging or starting the builder, declare and verify
`bootstrap-iso-debian` in a Platform Resource Control Plane registry:

```yaml
schema_version: 1
apps:
  bootstrap-iso-debian:
    enabled: true
    placement:
      builder_box: boxa
    ephemeral:
      privileged_approval: true
      expires_at: "2026-06-01T00:00:00Z"
      cleanup_required: true
```

Run the builder through:

```sh
ansible/bin/bootstrap-live-iso build-builder --resources-registry path/to/platform-resources.yml
```

The wrapper verifies the registry before `build-builder` or `all`.

Security requirements:

- the ISO and Alpine seed must stay secret-free
- the builder must use its own Tailnet identity and only the declared OOB SSH
  upload path
- the privileged container must be stopped and removed after use
- long-lived builder state requires a fresh registry approval with a new expiry
  timestamp

# Outputs

- a live system in RAM
- reliable hardware support for keyboard, storage, and NICs
- one usable network interface chosen automatically
- DHCP lease obtained
- `kk.local` and `klokast.local` published over mDNS to the selected DHCP LAN IP
- HTTP onboarding portal served from the Debian live system
- Tailscale joined with the bootstrap identity:
    - user: `root`
    - tag: `tag:bootstrap`
    - hostname: `<box>-bootstrap` (typically, the `<box>` is `yii` or `duh` or `b01` or `b02`)
- tailscale-ssh remote access available
- Python installed
- enough disk facts collected for Ansible to proceed

# Validation Target

Success for this bootstrap iso means:

1. it boots cleanly on a blank or dirty target SSD
2. keyboard, storage, and NICs work without SSD-state coupling
3. it reaches a normal console login
4. it obtains DHCP on one usable NIC
5. it joins Tailscale and becomes reachable as the bootstrap host
6. Ansible can run `ansible/playbooks/10-bootstrap-access.yml`
7. `ansible/playbooks/12-bootstrap-diskless-build.yml` can consume the versioned Alpine seed bundle

As a final step, the bootstrap system hands over to Ansible as a known remote target. The first host reboot happens later when `14-bootstrap-reboot-into-diskless.yml` switches the machine from the live bootstrap carrier to the SSD-backed diskless Alpine install.

# Clean up

The `apps/bootstrap-iso-debian/` application is only needed while enrolling a new box into the Platform. Generally, the installed files and outputs should be removed from the Platform after use.

If the user plan to enroll another box soon, then following files can be kept for a few weeks:
  - bootstrap ISO image
  - `alpine-standard-${release}-${arch}.tar.gz` tarball
  - builder container in `yii-bak`
