terraform {
  required_version = ">= 1.11.0"

  required_providers {
    vultr = {
      source  = "vultr/vultr"
      version = "~> 2.26"
    }
  }
}

provider "vultr" {}
