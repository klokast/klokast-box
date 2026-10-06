# Cloud runner tools

- `provision-vultr-ops` creates or converges the `vultr-ops` infra-agent host.
  See the [provisioning runbook](../runbooks/81-provision-vultr-ops.md).
- `provision-vultr-coder-guest` creates or converges a guest coding account on
  `vultr-ops`, with its own private GitHub repository and deploy key.
  See the [guest runbook](../runbooks/83-provision-vultr-coder-guest.md).

The MacBook application command wrapper is documented in the
[developer guide](../../klokast-dev/README.md#application-commands-with-kk).
The [CLI index](../../doc/cli-tools.md) identifies where each tool runs.
