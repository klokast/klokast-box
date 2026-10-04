- *Klokast* is a homelab platform: multi-sites but small (1 to 5 sites).

- Placeholders:
  - <box> : name of the box, for example `boxa` or `site-b`.
  - <cloud> : name of the cloud service provider for a VPC, for example `hetzner` or `vultr`.
  - <family> : name of a specific Platform deployment.

- *Platform* (with capital "P"): the private cloud solution described in this repository, including:
  - *box*: the unit of hardware of the Platform: one mini-pc, including its optional external peripherals: fan, SSD drives, etc. The box runs the Platform standard software stack: software stack: host OS, VMs, containers.
  - networking resources: overlay network, ISP router, network cables, etc.
  - external services that are tightly integrated to the other components of the Platform: deployment server, remote KVM, Cloudflare reverse proxy, AWS Glacier instance, etc.

- *site* : a location where boxes are deployed. By default, 1 site got 1 box only.

- *platform deployment*: a specific instance of the Platform, for example 2 boxes over 2 sites.

- *instance*: the private desired state of one Klokast deployment, stored in `klokast-instance.json`. It selects and configures capabilities provided by the Platform for that deployment. It contains deployment policy and topology, not Platform implementation, secrets, observed runtime state, generated configuration, or user data. Its machine-readable format is defined by the Platform's Instance schema.

- *machine*: the machines as seen by the Tailscale coordination server, and listed in the Tailscale admin console: onboarded host OS, VMs, containers, deployment server, NanoKVM devices, and endpoints (laptops and smartphones of admin and users).

- *internal users*: the "trusted" human users of the Platform, for example spouses and their children. Typically, they belong to the `group:owner`, `group:admin`, `group:operators` and `group:family` Tailscale groups.

- *external users*: don't belong to the trusted entity of the admins, for example anonymous users who visit a website hosted on the Platform. Typically, they belong to the `group:family` Tailscale group.

- *infra-agent* : AI-automated user with elevated privileges, for example users `smith` and `neo`. In development mode, it acts a a developer of the Platform. In production mode, it acts as a system administrator for the deployed platform.

- *user-agent*: both in production and development mode, AI-automated user with elevated privileges within a limited application service (e.g. a few related containers or a VM).

- *manufacturer*: the company that sells and ships the box, typically the OEM of the box, or the company that markets the Platform.

- *developer*: the humans that maintain the `klokast` upstream codebase.
