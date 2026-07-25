variable "server_name" {
  description = "Vultr instance hostname and label."
  type        = string
  default     = "vultr-ops"
}

variable "region" {
  description = "Vultr region ID."
  type        = string
  default     = "icn"
}

variable "plan" {
  description = "Vultr instance plan ID."
  type        = string
  default     = "vc2-1c-1gb"
}

variable "os_id" {
  description = "Vultr OS ID for Ubuntu 26.04 LTS x64."
  type        = number
  default     = 2760
}

variable "expected_os_name" {
  description = "Human-readable OS name corresponding to os_id."
  type        = string
  default     = "Ubuntu 26.04 LTS x64"
}

variable "bootstrap_ssh_key_name" {
  description = "Vultr SSH key name injected into the instance for initial root bootstrap."
  type        = string
  default     = "klokast-infra-vultr-ops"
}

variable "bootstrap_ssh_public_key" {
  description = "Public SSH key material for initial Vultr root bootstrap."
  type        = string
  sensitive   = false

  validation {
    condition     = can(regex("^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/=]+( .*)?$", trimspace(var.bootstrap_ssh_public_key)))
    error_message = "bootstrap_ssh_public_key must be an OpenSSH public key."
  }
}

variable "bootstrap_ssh_ipv4_cidrs" {
  description = "IPv4 CIDR ranges allowed to reach public bootstrap SSH."
  type        = set(string)

  validation {
    condition = length(var.bootstrap_ssh_ipv4_cidrs) > 0 && alltrue([
      for cidr in var.bootstrap_ssh_ipv4_cidrs : can(cidrhost(cidr, 0)) && length(regexall(":", cidr)) == 0
    ])
    error_message = "bootstrap_ssh_ipv4_cidrs must contain at least one IPv4 CIDR."
  }
}

variable "bootstrap_ssh_ipv6_cidrs" {
  description = "IPv6 CIDR ranges allowed to reach public bootstrap SSH."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for cidr in var.bootstrap_ssh_ipv6_cidrs : can(cidrhost(cidr, 0)) && length(regexall(":", cidr)) > 0
    ])
    error_message = "bootstrap_ssh_ipv6_cidrs must contain only IPv6 CIDRs."
  }
}

variable "enable_public_ipv4" {
  description = "Whether to provision a public IPv4 address."
  type        = bool
  default     = true
}

variable "enable_public_ipv6" {
  description = "Whether to provision a public IPv6 address."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Vultr instance tags."
  type        = list(string)
  default     = ["klokast", "infra-agent"]
}
