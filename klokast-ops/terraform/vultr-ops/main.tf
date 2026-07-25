locals {
  bootstrap_ssh_ipv4_rules = {
    for cidr in var.bootstrap_ssh_ipv4_cidrs : cidr => {
      subnet      = split("/", cidr)[0]
      subnet_size = tonumber(split("/", cidr)[1])
    }
  }

  bootstrap_ssh_ipv6_rules = {
    for cidr in var.bootstrap_ssh_ipv6_cidrs : cidr => {
      subnet      = split("/", cidr)[0]
      subnet_size = tonumber(split("/", cidr)[1])
    }
  }
}

resource "vultr_ssh_key" "bootstrap" {
  name    = var.bootstrap_ssh_key_name
  ssh_key = trimspace(var.bootstrap_ssh_public_key)
}

resource "vultr_firewall_group" "ops" {
  description = "${var.server_name}-firewall"
}

resource "vultr_firewall_rule" "bootstrap_ssh_ipv4" {
  for_each = local.bootstrap_ssh_ipv4_rules

  firewall_group_id = vultr_firewall_group.ops.id
  protocol          = "tcp"
  ip_type           = "v4"
  subnet            = each.value.subnet
  subnet_size       = each.value.subnet_size
  port              = "22"
  notes             = "Bootstrap SSH from the controller or operator"
}

resource "vultr_firewall_rule" "bootstrap_ssh_ipv6" {
  for_each = local.bootstrap_ssh_ipv6_rules

  firewall_group_id = vultr_firewall_group.ops.id
  protocol          = "tcp"
  ip_type           = "v6"
  subnet            = each.value.subnet
  subnet_size       = each.value.subnet_size
  port              = "22"
  notes             = "Bootstrap SSH from the controller or operator"
}

resource "vultr_firewall_rule" "tailscale_direct_ipv4" {
  firewall_group_id = vultr_firewall_group.ops.id
  protocol          = "udp"
  ip_type           = "v4"
  subnet            = "0.0.0.0"
  subnet_size       = 0
  port              = "41641"
  notes             = "Tailscale direct WireGuard traffic"
}

resource "vultr_firewall_rule" "tailscale_direct_ipv6" {
  firewall_group_id = vultr_firewall_group.ops.id
  protocol          = "udp"
  ip_type           = "v6"
  subnet            = "::"
  subnet_size       = 0
  port              = "41641"
  notes             = "Tailscale direct WireGuard traffic"
}

resource "vultr_instance" "ops" {
  label               = var.server_name
  hostname            = var.server_name
  region              = var.region
  plan                = var.plan
  os_id               = var.os_id
  ssh_key_ids         = [vultr_ssh_key.bootstrap.id]
  firewall_group_id   = vultr_firewall_group.ops.id
  backups             = "disabled"
  enable_ipv6         = var.enable_public_ipv6
  disable_public_ipv4 = !var.enable_public_ipv4
  activation_email    = false
  tags                = var.tags
}
