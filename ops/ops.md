## hardware of boxes
- CPU: x86-64 Intel Alder Lake i3-N305
- RAM: 32 GB
- SSD: 1 TB
- Network interfaces: 4 or 6
- Network controller: 2.5GbE Intel i226-V
- Manufacturer: CWWK
- Documentation:
  - `https://cwwk.net/products/6-lan-firewall-appliance-2-5g-router-12th-gen-intel-i3-n305-n100-ddr5-2-nvme-2-sata3-0-fanless-mini-pc-esxi-proxmox-host`
- UEFI: AMI Aptio, version `2.22.1287` or `2.22.1288`

## Box inventory

Box IDs, physical locations, countries, and site assignments are private
instance inputs. Read them from the private `klokast-instance.json`. Do not
copy them into this public operations document.

Each runtime machine uses its private box ID as the prefix and uses the public
runtime suffix rules in `doc/architecture.md`.

## Restart Tailscale on a remote dom0

Do not run a synchronous `rc-service tailscale stop` or `rc-service tailscale
restart` through Tailscale SSH. The stop action removes the transport before
the same remote shell can run the start action.

Use the checked-in dom0 convergence playbook. Its restart runs as a detached
Ansible async job. The controller waits for the host to reconnect and then
verifies the OpenRC service, the Tailscale backend state, and the daemon
version. Use the local console only when Tailscale is already stopped and the
controller cannot reach the host.
