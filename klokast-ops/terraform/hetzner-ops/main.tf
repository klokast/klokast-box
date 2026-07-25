resource "hcloud_firewall" "ops" {
  name   = "${var.server_name}-firewall"
  labels = merge(var.labels, { resource = "firewall" })

  rule {
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.bootstrap_ssh_source_ips
    description = "Bootstrap SSH from the admin workstation"
  }

  rule {
    direction   = "in"
    protocol    = "udp"
    port        = "41641"
    source_ips  = ["0.0.0.0/0", "::/0"]
    description = "Tailscale direct WireGuard traffic"
  }
}

resource "hcloud_server" "ops" {
  name         = var.server_name
  server_type  = var.server_type
  location     = var.location
  image        = var.image
  ssh_keys     = [var.bootstrap_ssh_key_name]
  firewall_ids = [tonumber(hcloud_firewall.ops.id)]
  labels       = var.labels

  public_net {
    ipv4_enabled = var.enable_public_ipv4
    ipv6_enabled = var.enable_public_ipv6
  }
}
