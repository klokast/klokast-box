# Torrent App

- Dedicated Alpine app VM: `<box>-torrent`, DMZ policy, `tag:vm,tag:torrent`.
- qBittorrent is the family UI/workload; expose it only via Tailscale Serve HTTPS 443.
- Mihomo owns VPN egress. Keep qBittorrent unprivileged and blocked by nftables unless traffic exits through the TUN device.
- Completed downloads stay on the torrent VM under `/srv/torrent/complete`; backend/media apps pull from it.
- Do not put torrent client state or upload services directly on `<box>-bak`, `<box>-dmz`, or dom0.
