#!/bin/bash
# Bitnami Django Deployment Script
# Run this after pulling code updates

echo "Setting up Django app on Bitnami..."

# Ensure we're in the right directory
cd /opt/bitnami/apache/htdocs/django-app

# Pull latest code
echo "Pulling latest code..."
git pull

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Run migrations
echo "Running migrations..."
python manage.py migrate

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Fix permissions for Apache/www-data
echo "Setting permissions..."
sudo chown -R www-data:www-data /opt/bitnami/apache/htdocs/django-app
sudo chmod -R 775 /opt/bitnami/apache/htdocs/django-app
sudo chmod 664 db.sqlite3

# Copy Apache config if it doesn't exist
if [ ! -f /opt/bitnami/apache/conf/vhosts/django-app.conf ]; then
    echo "Installing Apache configuration..."
    sudo cp django-app.conf /opt/bitnami/apache/conf/vhosts/
fi

# Restart Apache
echo "Restarting Apache..."
sudo /opt/bitnami/ctlscript.sh restart apache

echo "Deployment complete!"
echo "Your site should be available at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"