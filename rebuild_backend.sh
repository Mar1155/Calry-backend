#!/bin/bash
# Exit on error
set -e

echo "============================================="
echo "🚀 Rebuilding and restarting Calry backend..."
echo "============================================="

# Force a rebuild of the backend service and restart it in the background
docker compose up -d --build backend

echo "---------------------------------------------"
echo "✅ Backend container successfully built and started!"
echo "📡 Tailing logs (Ctrl+C to exit):"
echo "---------------------------------------------"

# Tail backend logs
docker compose logs -f backend
