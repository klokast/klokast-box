output "server_id" {
  description = "Hetzner Cloud server ID."
  value       = hcloud_server.ops.id
}

output "server_name" {
  description = "Provisioned server hostname."
  value       = hcloud_server.ops.name
}

output "ipv4_address" {
  description = "Public IPv4 address for the ops server."
  value       = hcloud_server.ops.ipv4_address
}
