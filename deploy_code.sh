#!/bin/bash

# Script to set up the Django application
cd /var/www/vdw-server

echo "Creating Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

echo "Setting up Django application..."
# Generate a new secret key for production
export SECRET_KEY=$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')

# Create .env file for production settings
cat > .env << EOF
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=*
EOF

# Collect static files
python manage.py collectstatic --noinput

# Run migrations
python manage.py migrate

echo "Django application setup complete!"