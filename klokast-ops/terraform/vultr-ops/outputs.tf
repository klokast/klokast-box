output "instance_id" {
  description = "Vultr instance ID."
  value       = vultr_instance.ops.id
}

output "server_name" {
  description = "Provisioned instance hostname."
  value       = vultr_instance.ops.hostname
}

output "ipv4_address" {
  description = "Public IPv4 address for vultr-ops."
  value       = vultr_instance.ops.main_ip
}

output "ipv6_address" {
  description = "Public IPv6 address for vultr-ops."
  value       = vultr_instance.ops.v6_main_ip
}

output "firewall_group_id" {
  description = "Vultr firewall group ID."
  value       = vultr_firewall_group.ops.id
}

output "ssh_key_id" {
  description = "Vultr bootstrap SSH key ID."
  value       = vultr_ssh_key.bootstrap.id
}
