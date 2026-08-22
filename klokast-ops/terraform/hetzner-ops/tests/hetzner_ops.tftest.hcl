mock_provider "hcloud" {}

run "hetzner_ops_exact_name" {
  command = plan

  assert {
    condition     = hcloud_server.ops.name == "hetzner-ops"
    error_message = "server name must be hetzner-ops"
  }

  assert {
    condition     = hcloud_firewall.ops.name == "hetzner-ops-firewall"
    error_message = "firewall name must derive from hetzner-ops"
  }
}

run "reject_hetzner_name_override" {
  command = plan

  variables {
    server_name = "ops"
  }

  expect_failures = [var.server_name]
}
