#!/bin/bash
# Initial deployment script - run ONCE when first setting up the server
# This creates everything from scratch

echo "=== INITIAL VDW SERVER DEPLOYMENT ==="
echo "WARNING: This will create a new database. Use update_code.sh for updates."
echo ""

# Check if already deployed
if [ -f "/var/www/vdw-server/deployed.flag" ]; then
    echo "ERROR: Server already deployed! Use update_code.sh for updates."
    exit 1
fi

# Update system
echo "Updating system packages..."
sudo apt update && sudo apt upgrade -y

# Install system dependencies
echo "Installing system dependencies..."
sudo apt install -y python3-pip python3-venv python3-dev
sudo apt install -y nginx supervisor git
sudo apt install -y build-essential libpq-dev

# Create application directory
echo "Creating application directory..."
sudo mkdir -p /var/www/vdw-server
sudo chown $USER:$USER /var/www/vdw-server
cd /var/www/vdw-server

# Clone repository (update with your repo URL)
echo "Cloning repository..."
# git clone https://github.com/YOUR_USERNAME/vdw-server.git .
# OR copy files manually if not using git

# Create directory for static files (media is on S3/CloudFront)
sudo mkdir -p /var/www/vdw-server/static

# Create Python virtual environment
echo "Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# Generate secret key for production
echo "Generating secret key..."
export SECRET_KEY=$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')

# Create .env file
echo "Creating environment configuration..."
cat > .env << EOF
# Django Settings
SECRET_KEY=$SECRET_KEY
DEBUG=False
ALLOWED_HOSTS=*

# S3/CloudFront Configuration
USE_S3_STORAGE=True
# Add your AWS credentials here or set them as environment variables
# AWS_ACCESS_KEY_ID=your-key
# AWS_SECRET_ACCESS_KEY=your-secret
# AWS_STORAGE_BUCKET_NAME=your-bucket
# AWS_CLOUDFRONT_DOMAIN=your-cloudfront-domain.cloudfront.net
EOF

echo ""
echo "IMPORTANT: Edit .env file to add your AWS S3/CloudFront credentials!"
echo ""

# Run initial migrations to create database
echo "Creating database..."
python app.py migrate

# Collect static files
echo "Collecting static files..."
python app.py collectstatic --noinput

# Create superuser (optional)
echo ""
echo "Would you like to create a superuser account? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    python app.py createsuperuser
fi

# Set permissions
echo "Setting permissions..."
sudo chown -R $USER:www-data /var/www/vdw-server
sudo chmod -R 755 /var/www/vdw-server
sudo chmod 664 /var/www/vdw-server/db.sqlite3

# Create deployment flag
touch /var/www/vdw-server/deployed.flag

echo ""
echo "=== INITIAL DEPLOYMENT COMPLETE ==="
echo "Next steps:"
echo "1. Configure Nginx (see nginx_config file)"
echo "2. Set up Gunicorn service (see gunicorn.service file)"
echo "3. Start services with: sudo systemctl start gunicorn && sudo systemctl start nginx"