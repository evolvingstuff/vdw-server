#!/bin/bash

# Initial server setup script
echo "Starting VDW Server deployment setup..."

# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and system dependencies
sudo apt install -y python3-pip python3-venv python3-dev
sudo apt install -y nginx supervisor
sudo apt install -y build-essential libpq-dev

# Install git for cloning (if needed)
sudo apt install -y git

# Create application directory
sudo mkdir -p /var/www/vdw-server
sudo chown $USER:$USER /var/www/vdw-server

# Create directory for static files
sudo mkdir -p /var/www/vdw-server/static
sudo mkdir -p /var/www/vdw-server/media

# Set proper permissions
sudo chown -R $USER:www-data /var/www/vdw-server
sudo chmod -R 755 /var/www/vdw-server

echo "Basic setup complete!"
echo "Next: Upload your code to /var/www/vdw-server/"