# =============================================================================
# versions.tf
# -----------------------------------------------------------------------------
# Backend block is intentionally PARTIAL (no bucket/region values here).
# Terraform backend blocks cannot reference variables or locals -- that's a
# hard language limitation, not a choice -- so the values are supplied at
# `terraform init` time via a backend config file. This is what actually
# fixes the "hardcoded region interpolation" problem flagged in review: there
# is no interpolation here to break, because there's nothing hardcoded here
# at all. See backend.hcl.example.
# =============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  backend "s3" {
    # Populated via: terraform init -backend-config=backend.hcl
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
    }
  }
}
