#!/bin/bash

echo "⚡Starting services..."

docker compose --env-file ../.env -f ../infrastructure/docker/docker-compose.yml up -d

echo "⌚ waiting for services to initialize..."
sleep 5

docker compose --env-file ../.env -f ../infrastructure/docker/docker-compose.yml ps

echo "✅ Services started"