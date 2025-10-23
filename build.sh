#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Running database migrations..."
# This runs makemigrations and migrate only on the initial deployment
python manage.py migrate --noinput

echo "--- Collecting static files..."
# Collects all CSS/JS files into the 'staticfiles' directory
python manage.py collectstatic --noinput

echo "--- Deployment build steps complete. ---"
