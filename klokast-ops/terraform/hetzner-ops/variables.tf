variable "server_name" {
  description = "Hetzner Cloud server hostname."
  type        = string
  default     = "ops"
}

variable "server_type" {
  description = "Hetzner Cloud server type."
  type        = string
  default     = "cx23"
}

variable "location" {
  description = "Hetzner Cloud location."
  type        = string
  default     = "hel1"
}

variable "image" {
  description = "Hetzner Cloud image name."
  type        = string
  default     = "ubuntu-24.04"
}

variable "bootstrap_ssh_key_name" {
  description = "Existing Hetzner Cloud SSH key name injected into the server."
  type        = string
  default     = "xiaoju_codex_hetzner"
}

variable "bootstrap_ssh_source_ips" {
  description = "CIDR ranges allowed to reach public bootstrap SSH on the ops server."
  type        = set(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_public_ipv4" {
  description = "Whether to provision a public IPv4."
  type        = bool
  default     = true
}

variable "enable_public_ipv6" {
  description = "Whether to provision a public IPv6."
  type        = bool
  default     = false
}

variable "labels" {
  description = "Optional Hetzner Cloud labels."
  type        = map(string)
  default = {
    managed-by = "terraform"
    project    = "klokast-ops"
    role       = "ops"
  }
}
