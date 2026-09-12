# =============================================================================
# variables.tf
# One file, one set of variables. If you ever genuinely need a second
# environment, override these with `-var-file=prod.tfvars` -- that's the
# "workspace/DRY root config" pattern your own review recommended, and it
# doesn't require a second copy of every .tf file to get there.
# =============================================================================

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "financial-ai"
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "azs" {
  description = "2 AZs is the practical minimum for EKS + RDS Multi-AZ; 3 only if you have a specific reason."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
}

variable "enable_ha_nat" {
  description = <<-EOT
    false (default): ONE NAT gateway shared across all private subnets.
    Cheaper, and the right default for a single workload -- this is the fix
    for the "silently spins up multiple expensive NAT gateways" issue: it no
    longer happens unless you deliberately opt in.
    true: one NAT gateway per AZ, for when a NAT becoming unavailable
    actually needs to not take down every private subnet at once.
  EOT
  type    = bool
  default = false
}

variable "eks_cluster_version" {
  type    = string
  default = "1.31"
}

variable "eks_node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "eks_node_min_size" {
  type    = number
  default = 2
}

variable "eks_node_max_size" {
  type    = number
  default = 6
}

variable "eks_node_desired_size" {
  type    = number
  default = 2
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 50
}

variable "db_name" {
  type    = string
  default = "financial_ai"
}

variable "db_username" {
  type    = string
  default = "finai"
}

variable "db_deletion_protection" {
  description = "true for real production. Override to false only for a deliberately temporary/classroom stack -- see the teardown walkthrough."
  type        = bool
  default     = true
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.small"
}
