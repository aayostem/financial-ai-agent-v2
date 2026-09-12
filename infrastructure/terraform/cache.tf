# =============================================================================
# cache.tf
# One replication group with one replica -- enough for automatic failover
# without paying for a topology this workload doesn't need yet.
# =============================================================================

resource "random_password" "redis" {
  length  = 24
  special = false
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-redis-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Redis from EKS nodes"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.project_name}-redis"
  description           = "Financial AI agent cache/dedup store"

  engine         = "redis"
  engine_version = "7.1"
  node_type      = var.redis_node_type

  num_cache_clusters = 2 # primary + 1 replica -- automatic failover, nothing more
  automatic_failover_enabled = true

  subnet_group_name = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  auth_token                 = random_password.redis.result
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
}
