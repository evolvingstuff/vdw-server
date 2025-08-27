#!/bin/bash
# Bitnami update script - for code updates on Bitnami stack
# This preserves the existing database

echo "=== UPDATING DJANGO APP ON BITNAMI ==="
echo "This will preserve your existing database."
echo ""

cd /opt/bitnami/apache/htdocs/django-app

# Backup database before update
echo "Backing up database..."
cp db.sqlite3 db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)

# Pull latest code
echo "Pulling latest code..."
git pull

# Activate virtual environment
source venv/bin/activate

# Update dependencies
echo "Updating dependencies..."
pip install -r requirements.txt

# Run migrations (preserves existing data)
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

# Restart Apache
echo "Restarting Apache..."
sudo /opt/bitnami/ctlscript.sh restart apache

echo ""
echo "=== UPDATE COMPLETE ==="
echo "Database backup saved as: db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)"
echo "Your site should be available at http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4)"