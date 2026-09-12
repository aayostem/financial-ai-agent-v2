resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project_name}/config"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL   = "postgresql+asyncpg://${var.db_username}:${random_password.db.result}@${aws_db_instance.main.address}:5432/${var.db_name}"
    POSTGRES_HOST  = aws_db_instance.main.address
    POSTGRES_PORT  = "5432"
    POSTGRES_USER  = var.db_username
    POSTGRES_PASSWORD = random_password.db.result
    POSTGRES_DB    = var.db_name
    REDIS_HOST     = aws_elasticache_replication_group.main.primary_endpoint_address
    REDIS_PORT     = "6379"
    REDIS_PASSWORD = random_password.redis.result
    REDIS_DB       = "0"
  })
}
