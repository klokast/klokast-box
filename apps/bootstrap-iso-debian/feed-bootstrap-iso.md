Shape of the bootstrap ISO: `klokast-bootstrap-debian-<debian-release>-amd64.iso`

If the ISO and its `.sha512` sidecar are already present under `/data` on the
NanoKVM, the deployment server can inspect the available media and load an
explicit ISO as CD-ROM:

```sh
ansible/bin/nanokvm-virtual-media --status
ansible/bin/nanokvm-virtual-media --load klokast-bootstrap-debian-trixie-amd64.iso --verify-sha512-sidecar
```

To fetch an ISO directly from the internet onto NanoKVM first:

```sh
BOOTSTRAP_ISO_URL='https://downloads.example.invalid/klokast-bootstrap-debian-trixie-amd64.iso'
BOOTSTRAP_ISO_SHA512_URL="${BOOTSTRAP_ISO_URL}.sha512"
ansible/bin/nanokvm-virtual-media --upload-url "$BOOTSTRAP_ISO_URL" \
  --as klokast-bootstrap-debian-trixie-amd64.iso \
  --sha512-url "$BOOTSTRAP_ISO_SHA512_URL"
```

To unload the ISO before booting from the installed SSD:

```sh
ansible/bin/nanokvm-virtual-media --unload
```

The role of the bootstrap ISO is to hand off the installation to the Ansible playbooks operated from the deployment server:

  0. console-only, amd64 architecture
  1. Boot reliably on clean or dirty hardware
  2. Bring up networking:
    2.1 Identify a usable NIC
    2.1 Bring the NIC up
    2.1 Get an IP from DHCP
  3. Publish the DHCP address over mDNS as `kk.local` and `klokast.local`
  4. Serve the HTTP onboarding portal
  5. Bring up remote management
    5.1 Start `tailscaled`
    5.2 Wait for the operator to enter the node name and `tskey-auth-*`
    5.3 Run `tailscale up --ssh --hostname=<node>-bootstrap --advertise-tags=tag:bootstrap --auth-key=<key>`
    5.4 Install Python, which is required from Ansible targets.
  6. Hand off to Ansible as a known remote target for the next stages.

The bootstrap ISO should stay minimal and generic:

  It must not include:
    - box name
    - Xen dom0 runtime, VM images, Podman workloads.
    - Tailscale auth keys, OAuth material, or other secrets

  It includes:
    - Python
    - disk tools
    - DHCP client tooling
    - Tailscale
    - Avahi mDNS tooling
    - a compiled Go onboarding portal
    - small systemd services that:
      - chooses a usable NIC
      - gets DHCP
      - starts `tailscaled`
      - publishes `kk.local` and `klokast.local`
      - serves the onboarding portal

The bootstrap ISO is Debian based (console-only Debian live image, custom built with Debian’s `live-build`), while the target system it installs is Alpine Linux. Using Debian rather than Alpine as bootstrap carrier is better because:
- it is more robust: it boots better than Alpine from SSD that are not perfectly blank.
- as the bootstrap iso is only a temporary control environment, it better not already behave like the final Alpine diskless host, this avoids leaks between the systems.
