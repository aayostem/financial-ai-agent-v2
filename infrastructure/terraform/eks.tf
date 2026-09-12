# =============================================================================
# eks.tf
# =============================================================================

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.31"

  cluster_name    = "${var.project_name}-eks"
  cluster_version = var.eks_cluster_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Managed node groups over Karpenter/self-managed nodes for the same
  # reason the review flagged elsewhere: this is one workload, not a
  # multi-tenant platform. Karpenter's value is dynamic bin-packing across
  # heterogeneous workloads -- a fixed-size managed node group is simpler,
  # cheaper to reason about, and entirely sufficient here. Revisit if node
  # utilization data ever actually shows a need for it.
  eks_managed_node_groups = {
    default = {
      instance_types = var.eks_node_instance_types
      min_size       = var.eks_node_min_size
      max_size       = var.eks_node_max_size
      desired_size   = var.eks_node_desired_size
    }
  }

  # Enables the OIDC provider IRSA depends on -- this is what iam.tf's
  # roles attach to.
  enable_irsa = true

  cluster_endpoint_public_access = true
}
