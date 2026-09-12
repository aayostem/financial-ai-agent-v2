echo "🔴 Stopping all services..."
docker compose -f infrastructure/docker/docker-compose.yml down

echo "✅ Services stopped!"