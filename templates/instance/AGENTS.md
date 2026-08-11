# Contract v1 instance rules

- Keep this repository private and standalone.
- Never commit secrets, credentials, key material, generated output, or runtime
  state.
- Treat `klokast.yml`, `klokast.lock.yml`, `ops/deployment.yml`, and
  `ops/platform-resources.yml` as the only authoritative Contract v1 inputs.
- Use stable logical box IDs for references and `hostname_prefix` only for
  derived runtime names.
- Declare a capability before enabling it. Enabled and prohibited capabilities
  must be disjoint; declared and prohibited capabilities may overlap.
- Bind only applications and resource IDs declared by the engine's embedded
  public app manifests.
- Use `Etc/UTC` for every site. Platform inputs do not select a timezone.
- Validate with `klokast check --instance PATH` before review.
- Use `klokast plan --instance PATH --compatibility-deployment FILE
  --compatibility-registry FILE --compatibility-controller-ha FILE` only for an
  offline, read-only compatibility report. Add `--observation FILE` to create
  Plan v1. Neither form authorizes migration or apply changes.
- Do not add private `apps/`, `extensions/`, site executors, generated
  inventories, or policy override interfaces to Contract v1.
