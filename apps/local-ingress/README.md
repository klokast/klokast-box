# Local Ingress

DMZ-local HTTPS ingress for the dumb AP design.

## Model

- Runs nginx on `<box>-dmz`.
- Listens on TCP 443 for `music.<domain>`, `nextcloud.<domain>`, and `immich.<domain>`.
- Uses a controller-provided wildcard certificate/key for `*.<domain>`.
- Proxies to the backend VM on exact app upstream ports declared by the app
  manifest.
- Household Wi-Fi clients reach it only through router/app-resource policy.

Declare the application present in the private Instance. This fragment shows
its `apps` entry; keep the other required Instance fields:

```json
{
  "apps": {
    "local-ingress": {
      "desired-state": "present",
      "placement": {
        "mode": "single-box",
        "box": "boxb"
      }
    }
  }
}
```

Validate and publish desired-state changes through the
[Instance workflow](../../doc/klokast-instance-specification.md). The controller
tools consume the validated resource view derived from Instance.
The app manifest requires the `local-lan` capability. The current Instance
schema cannot declare that capability. Installation must stop until Instance
and its resource projection support these required flows. Do not add
unsupported connectivity values.

After the required resource support is available, deploy from the controller
as `smith`:

```sh
apps/local-ingress/bin/local-ingressctl deploy \
  --box boxb \
  --resources-registry ~/private/klokast/platform-resources.yml \
  --local-domain home.example.com \
  --tls-cert ~/private/klokast/certs/home.example.com/fullchain.pem \
  --tls-key ~/private/klokast/certs/home.example.com/privkey.pem
```
