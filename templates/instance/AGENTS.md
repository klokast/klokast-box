# Klokast Instance Specification v1 rules

- Keep this repository private and standalone.
- Treat `klokast-instance.json` and `klokast.lock.json` as the only
  authoritative instance inputs.
- Never commit secrets, credentials, key material, generated output, runtime
  status, or user data.
- Do not add a timezone. Platform time is always `Etc/UTC`.
- Use a box ID such as `boxa` as its runtime hostname prefix.
- Put `site`, `country`, and `description` directly in each box. Do not add a
  top-level site catalog. Boxes that use the same site label must use identical
  site metadata.
- Select only connectivity capabilities that the locked public engine defines.
- Include `overlay` on every box.
- Bind only apps, placement modes, features, and data IDs that public app
  manifests define.
- Use `desired-state: absent` only with declared retained data. Remove the app
  entry after its declared data is removed.
- Never treat omission as permission to delete unknown storage.
- Do not edit the engine lock by hand.
- Run `klokast check --instance PATH` before review and commit.
- `klokast plan` and `klokast doctor` are read-only. They do not authorize an
  apply or migration.
