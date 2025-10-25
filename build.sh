#!/usr/bin/env bash
# Exit immediately if a command exits with a non-zero status.
set -e

echo "--- Running database migrations..."
python manage.py migrate --noinput

# --- FIX: Using the simplest loaddata command without encoding flag ---
echo "--- Loading initial clinic data..."
python manage.py loaddata initial_data.json
# --- END FIX ---

echo "--- Collecting static files..."
python manage.py collectstatic --noinput
