# =============================================================================
# state-backend/main.tf
# -----------------------------------------------------------------------------
# Bootstraps the S3 bucket + DynamoDB table that the main stack's backend
# uses to store and lock its state. This has to be a SEPARATE tiny stack,
# applied once, manually, before anything else -- Terraform can't create the
# bucket it's about to store its own state in without a chicken-and-egg
# problem. This isn't over-engineering; it's a Terraform limitation, and
# folding it into the main stack would just hide that limitation, not solve it.
#
# Run once:
#   cd state-backend && terraform init && terraform apply
# Then never touch this again unless the bucket/table need to change.
# =============================================================================

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "financial-ai"
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "terraform_state" {
  bucket = "${var.project_name}-terraform-state"

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "terraform_state" {
  bucket                  = aws_s3_bucket.terraform_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_dynamodb_table" "terraform_lock" {
  name         = "${var.project_name}-terraform-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

output "state_bucket" {
  value = aws_s3_bucket.terraform_state.bucket
}

output "lock_table" {
  value = aws_dynamodb_table.terraform_lock.name
}
