#!/bin/sh
set -eu

echo "Applying database migrations before server startup..."
python manage.py migrate --noinput

echo "Collecting static files before server startup..."
python manage.py collectstatic --noinput

exec "$@"
