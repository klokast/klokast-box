# Torrent

Dedicated torrent download edge for a box.

## Model

- `<box>-torrent`: Alpine app VM in the DMZ, `tag:torrent`.
- qBittorrent-nox: WebUI at `https://<box>-torrent.<tailnet>`.
- Mihomo: VM-local VPN egress in TUN mode.
- nftables: qBittorrent UID may only egress through the VPN TUN device.
- Completed files: `/srv/torrent/complete`; media apps pull from there.

Enable in the private platform-resource registry:

```yaml
apps:
  torrent:
    enabled: true
    placement:
      active_master: k002
    app_vms:
      torrent:
        k002:
          vm_ipv4_address: 192.168.200.30
    resources: {}
```

Deploy from the controller as `smith`:

```sh
apps/torrent/bin/torrentctl deploy \
  --box k002 \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --vpn-config ~/private/klokast/torrent-vpn.yml
```

Open the UI from an allowed Tailscale device:

```sh
klokast-dev/bin/kk torrent open --to k002
```

## Notes

- Family access is via Tailscale HTTPS only; qBittorrent itself listens on localhost.
- The default WebUI local-auth bypass is intentional behind Tailscale Serve.
- `torrent-export` has read-only access to completed files for pull-based imports.
- Keep stale Tailscale machines cleaned after app VM creation or rename.
