# Local Ingress

- Runs on the standard DMZ VM as an nginx host service.
- Exposes only LAN-presence HTTPS for the household realm.
- Requires box access policy selecting `local-presence-control: local-lan`;
  AP discovery alone is not authority to deploy it.
- Nextcloud/Immich upstream firewall resources require the separate
  `private-service-ingress: local-lan` policy.
- TLS certificate/key are controller-local inputs; do not store private key material in the repo.
- Public/Tailnet app ingresses remain owned by their app-specific services.
