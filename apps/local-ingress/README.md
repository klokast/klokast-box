# Local Ingress

DMZ-local HTTPS ingress for the dumb AP design.

## Model

- Runs nginx on `<box>-dmz`.
- Listens on TCP 443 for `music.<domain>`, `nextcloud.<domain>`, and `immich.<domain>`.
- Uses a controller-provided wildcard certificate/key for `*.<domain>`.
- Proxies to the backend VM on exact app upstream ports declared by the app
  manifest.
- Household Wi-Fi clients reach it only through router/app-resource policy.

Enable in the private platform-resource registry:

```yaml
boxes:
  boxb:
    access:
      available_capabilities: [overlay, local-lan]
      enabled_capabilities: [overlay, local-lan]
apps:
  local-ingress:
    enabled: true
    placement:
      active_master: boxb
    resources: {}
```

This enables all required local-ingress resources. The app manifest, not the
box capability list, selects the exact application flows.

Deploy from the controller as `smith`:

```sh
apps/local-ingress/bin/local-ingressctl deploy \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --local-domain home.example.com \
  --tls-cert ~/private/klokast/certs/home.example.com/fullchain.pem \
  --tls-key ~/private/klokast/certs/home.example.com/privkey.pem
```
