#!/bin/bash
# Lightsail Django Pre-configured Stack Deployment Script
# Works for both initial deployment and updates

echo "=== DEPLOYING DJANGO APP ON LIGHTSAIL DJANGO STACK ==="

cd /opt/bitnami/apache/htdocs/django-app

# Backup database if it exists
if [ -f "db.sqlite3" ]; then
    echo "Backing up existing database..."
    cp db.sqlite3 db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)
    echo "Database backed up."
fi

# Pull latest code
echo "Pulling latest code..."
git pull

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
echo "Installing/updating dependencies..."
pip install -r requirements.txt

# Run migrations
echo "Running database migrations..."
python app.py migrate

# Collect static files
echo "Collecting static files..."
python app.py collectstatic --noinput

# Fix permissions for Apache/www-data
echo "Setting permissions..."
sudo chown -R www-data:www-data /opt/bitnami/apache/htdocs/django-app
sudo chmod -R 775 /opt/bitnami/apache/htdocs/django-app
sudo chmod 664 db.sqlite3

# Copy Apache config if it doesn't exist (first deployment)
if [ ! -f /opt/bitnami/apache/conf/vhosts/django-app.conf ]; then
    echo "Installing Apache configuration..."
    sudo cp django-app.conf /opt/bitnami/apache/conf/vhosts/
fi

# Restart Apache
echo "Restarting Apache..."
sudo /opt/bitnami/ctlscript.sh restart apache

echo ""
echo "=== DEPLOYMENT COMPLETE ==="
echo "Your site should be available at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"