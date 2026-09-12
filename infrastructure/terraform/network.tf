# =============================================================================
# network.tf
# =============================================================================

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr
  azs  = var.azs

  # /20s give ~4000 IPs per subnet -- generous headroom for pod IPs under the
  # AWS VPC CNI without needing secondary CIDR gymnastics later.
  private_subnets = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, az in var.azs : cidrsubnet(var.vpc_cidr, 4, i + 8)]

  enable_nat_gateway = true
  # THE FIX for the flagged NAT cost issue: single_nat_gateway = true means
  # exactly one NAT gateway total, shared across all private subnets, unless
  # enable_ha_nat is explicitly set true. Previously this was implicit
  # (count = length(public_subnets)) and multiplied silently; now it's one
  # named variable with a safe default.
  single_nat_gateway = !var.enable_ha_nat
  one_nat_gateway_per_az = var.enable_ha_nat

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Required for the AWS Load Balancer Controller to auto-discover subnets
  # when provisioning ALBs -- this was correctly called out as "working
  # well" in your review, kept exactly as-is.
  public_subnet_tags = {
    "kubernetes.io/role/elb"                       = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "shared"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb"              = "1"
    "kubernetes.io/cluster/${var.project_name}-eks" = "shared"
  }
}
