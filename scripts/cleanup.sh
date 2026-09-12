echo "Cleaning up..."

read -p "Are you sure? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "🗑️ Removing containers and volumes..."
    docker compose -f infrastructure/docker/docker-compose.yml down -v
    echo "✅ Cleanup completed!"
fi