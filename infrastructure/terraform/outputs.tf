# =============================================================================
# outputs.tf
# =============================================================================

output "cluster_name" {
  value = module.eks.cluster_name
}

output "configure_kubectl" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}

output "redis_endpoint" {
  value     = aws_elasticache_replication_group.main.primary_endpoint_address
  sensitive = false
}

output "secrets_manager_secret_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "external_secrets_irsa_role_arn" {
  value = module.irsa_external_secrets.iam_role_arn
}

output "app_irsa_role_arn" {
  value = module.irsa_app.iam_role_arn
}

output "s3_buckets" {
  value = { for k, b in aws_s3_bucket.this : k => b.bucket }
}
