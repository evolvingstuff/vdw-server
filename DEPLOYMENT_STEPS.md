# Deployment Steps for VDW Server on AWS Lightsail

Follow these steps in order using the browser-based SSH terminal:

## Step 1: Initial Server Setup
Copy and paste this entire block into your SSH terminal:
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3-pip python3-venv python3-dev nginx git build-essential

# Create application directory
sudo mkdir -p /var/www/vdw-server
sudo chown $USER:$USER /var/www/vdw-server
```

## Step 2: Upload Your Code
Since you're using browser SSH, the easiest way is to use git. First, push your code to GitHub, then:
```bash
cd /var/www/vdw-server
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git .
```

**Alternative: Manual Upload**
If you don't want to use git, create the files manually:
```bash
cd /var/www/vdw-server
```
Then use `nano` or `vim` to create each file and paste the content.

## Step 3: Set Up Python Environment
```bash
cd /var/www/vdw-server
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

## Step 4: Configure Django for Production
```bash
# Generate secret key
export SECRET_KEY=$(python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')
echo "SECRET_KEY=$SECRET_KEY" > .env

# Collect static files
python manage.py collectstatic --noinput

# Run migrations
python manage.py migrate

# Create superuser (optional)
python manage.py createsuperuser
```

## Step 5: Set Up Gunicorn
```bash
# Create log directory
sudo mkdir -p /var/log/gunicorn
sudo chown $USER:www-data /var/log/gunicorn

# Copy the gunicorn service file
sudo cp gunicorn.service /etc/systemd/system/vdw-server.service

# Start and enable the service
sudo systemctl daemon-reload
sudo systemctl start vdw-server
sudo systemctl enable vdw-server
sudo systemctl status vdw-server
```

## Step 6: Configure Nginx
```bash
# Copy nginx configuration
sudo cp nginx_config /etc/nginx/sites-available/vdw-server
sudo ln -s /etc/nginx/sites-available/vdw-server /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test and restart nginx
sudo nginx -t
sudo systemctl restart nginx
```

## Step 7: Configure Firewall (if needed)
```bash
# Open HTTP port in Lightsail networking settings
# The instance should already have port 80 open by default
```

## Step 8: Test Your Deployment
1. Get your Lightsail instance's public IP from the AWS console
2. Visit: http://YOUR_INSTANCE_IP
3. Admin panel: http://YOUR_INSTANCE_IP/admin

## Troubleshooting Commands
```bash
# Check gunicorn logs
sudo journalctl -u vdw-server -f

# Check nginx logs
sudo tail -f /var/log/nginx/error.log

# Restart services
sudo systemctl restart vdw-server
sudo systemctl restart nginx

# Check service status
sudo systemctl status vdw-server
sudo systemctl status nginx
```

## Updating Your Code
When you need to update your application:
```bash
cd /var/www/vdw-server
git pull  # or manually update files
source venv/bin/activate
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate
sudo systemctl restart vdw-server
```