# =============================================================================
# database.tf
# pgvector is natively supported on RDS Postgres 15.2+/14.7+/13.11+ -- no
# custom parameter group or allowlist entry needed, just `CREATE EXTENSION
# vector;` once connected. Run that once via psql/a migration after apply.
# =============================================================================

resource "random_password" "db" {
  length  = 24
  special = false # avoid characters that need URL-encoding in DATABASE_URL
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_name}-db"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db" {
  name_prefix = "${var.project_name}-db-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from EKS nodes"
    from_port       = 5432
    to_port         = 5432
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

resource "aws_db_instance" "main" {
  identifier     = "${var.project_name}-postgres"
  engine         = "postgres"
  engine_version = "16"

  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.db_name
  username = var.db_username
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  multi_az                = true
  backup_retention_period = 7
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.project_name}-postgres-final"

  apply_immediately = false
}
