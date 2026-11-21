terraform {
  required_version = ">= 1.7.0"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.50"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # State lives in a versioned Scaleway Object Storage bucket. The bucket is
  # created manually (see README) and configured via `-backend-config`, so no
  # account-specific values are committed.
  backend "s3" {}
}
