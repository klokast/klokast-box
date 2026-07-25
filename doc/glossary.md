- *Klokast* is a homelab platform: multi-sites but small (1 to 5 sites).

- Placeholders:
  - <box> : name of the box, for example `k001` or `milla`.
  - <cloud> : name of the cloud service provider for a VPC, for example `hetzner` or `vultr`.
  - <family> : name of a specific Platform deployment.

- *Platform* (with capital "P"): the private cloud solution described in this repository, including:
  - *box*: the unit of hardware of the Platform: one mini-pc, including its optional external peripherals: fan, SSD drives, etc. The box runs the Platform standard software stack: software stack: host OS, VMs, containers.
  - networking resources: overlay network, ISP router, network cables, etc.
  - external services that are tightly integrated to the other components of the Platform: deployment server, remote KVM, Cloudflare reverse proxy, AWS Glacier instance, etc.

- *site* : a location where boxes are deployed. By default, 1 site got 1 box only.

- *Platform deployment*: a specific instance of the Platform, for example 2 boxes over 2 sites.

- *machine*: the machines as seen by the Tailscale coordination server, and listed in the Tailscale admin console: onboarded host OS, VMs, containers, deployment server, NanoKVM devices, and endpoints (laptops and smartphones of admin and users).

- *internal users*: the "trusted" users of the Platform, for example spouses and their children.

- *admin* : internal user with elevated privileges. Typically, he belongs to the `group:owner`, `group:admin`, `group:operators` and `group:family` Tailscale groups.

- *external users*: don't belong to the trusted entity of the admins, for example anonymous users who visit a website hosted on the Platform. Typically, they belong to the `group:family` Tailscale group.

- *manufacturer*: the company that sells and ships the box, typically the OEM of the box, or the company that markets the Platform.

- *developer*: the person or entity that maintains the `klokast` upstream codebase.
