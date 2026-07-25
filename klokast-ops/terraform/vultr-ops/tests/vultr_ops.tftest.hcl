mock_provider "vultr" {}

variables {
  bootstrap_ssh_public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestOnlyTestOnlyTestOnlyTestOnlyTestOnlyTestOnly"
  bootstrap_ssh_ipv4_cidrs = ["198.51.100.10/32"]
  bootstrap_ssh_ipv6_cidrs = ["2001:db8::10/128"]
}

run "vultr_ops_shape" {
  command = plan

  assert {
    condition     = vultr_instance.ops.hostname == "vultr-ops"
    error_message = "instance hostname must be vultr-ops"
  }

  assert {
    condition     = vultr_instance.ops.label == "vultr-ops"
    error_message = "instance label must be vultr-ops"
  }

  assert {
    condition     = vultr_instance.ops.region == "icn"
    error_message = "instance must be in the Vultr Seoul region"
  }

  assert {
    condition     = vultr_instance.ops.plan == "vc2-1c-1gb"
    error_message = "instance must use the requested shared CPU plan"
  }

  assert {
    condition     = vultr_instance.ops.os_id == 2760
    error_message = "instance must use Vultr OS ID 2760 for Ubuntu 26.04 LTS x64"
  }

  assert {
    condition     = vultr_instance.ops.enable_ipv6 == true
    error_message = "public IPv6 must be enabled"
  }

  assert {
    condition     = vultr_instance.ops.disable_public_ipv4 == false
    error_message = "public IPv4 must be enabled"
  }

  assert {
    condition     = vultr_instance.ops.backups == "disabled"
    error_message = "automatic backups must be disabled"
  }

}

run "firewall_rules" {
  command = plan

  assert {
    condition     = vultr_firewall_rule.bootstrap_ssh_ipv4["198.51.100.10/32"].port == "22"
    error_message = "IPv4 bootstrap SSH must be limited to tcp/22"
  }

  assert {
    condition     = vultr_firewall_rule.bootstrap_ssh_ipv4["198.51.100.10/32"].subnet == "198.51.100.10"
    error_message = "IPv4 bootstrap SSH rule must use the requested source subnet"
  }

  assert {
    condition     = vultr_firewall_rule.bootstrap_ssh_ipv6["2001:db8::10/128"].port == "22"
    error_message = "IPv6 bootstrap SSH must be limited to tcp/22"
  }

  assert {
    condition     = vultr_firewall_rule.tailscale_direct_ipv4.port == "41641" && vultr_firewall_rule.tailscale_direct_ipv4.protocol == "udp"
    error_message = "IPv4 Tailscale direct traffic must allow udp/41641"
  }

  assert {
    condition     = vultr_firewall_rule.tailscale_direct_ipv6.port == "41641" && vultr_firewall_rule.tailscale_direct_ipv6.protocol == "udp"
    error_message = "IPv6 Tailscale direct traffic must allow udp/41641"
  }
}
