#!/bin/bash
# Code update script - run this for updates AFTER initial deployment
# This preserves the existing database

echo "=== UPDATING VDW SERVER CODE ==="
echo "This will preserve your existing database."
echo ""

# Check if initial deployment was done
if [ ! -f "/var/www/vdw-server/deployed.flag" ]; then
    echo "ERROR: Server not deployed yet! Run deploy_initial.sh first."
    exit 1
fi

cd /var/www/vdw-server

# Backup database before update
echo "Backing up database..."
cp db.sqlite3 db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)

# Activate virtual environment
source venv/bin/activate

# Pull latest code (if using git)
echo "Pulling latest code..."
git pull
# OR manually update files if not using git

# Install/update dependencies
echo "Updating dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Run migrations (this preserves existing data)
echo "Running database migrations..."
python app.py migrate

# Collect static files
echo "Collecting static files..."
python app.py collectstatic --noinput

# Fix permissions
echo "Fixing permissions..."
sudo chown -R $USER:www-data /var/www/vdw-server
sudo chmod -R 755 /var/www/vdw-server
sudo chmod 664 /var/www/vdw-server/db.sqlite3

# Restart services
echo "Restarting services..."
sudo systemctl restart gunicorn
sudo systemctl restart nginx

echo ""
echo "=== UPDATE COMPLETE ==="
echo "Database backup saved as: db.sqlite3.backup.$(date +%Y%m%d_%H%M%S)"
echo "Your site should be live with the updates."