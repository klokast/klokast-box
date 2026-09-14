# Terraform checks

This directory contains the Terraform modules for supported cloud infra-agent
hosts. Each module keeps its provider selections in a committed dependency
lock file and keeps its tests in `tests/`.

The `Terraform` GitHub Actions workflow checks each module. It uses a fixed
Terraform release and actions pinned to full Git commits. It runs:

```sh
terraform fmt -check -recursive
terraform init -backend=false -input=false -lockfile=readonly
terraform validate
terraform test
```

The tests use Terraform mock providers. The workflow has read-only repository
permission. It has no cloud credential, backend, private state, or deployment
authority. Do not add `terraform plan`, `terraform apply`, provider
credentials, repository secrets, or state access to this workflow.

Run real provisioning only from the trusted operator path in the applicable
module runbook. Keep Terraform and provider updates in reviewed changes. Update
the workflow version, module constraints, lock files, and tests together when
the interface requires it.
