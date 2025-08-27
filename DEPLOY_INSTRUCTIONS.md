# Deploy Django Blog to AWS Lightsail - Step by Step

Open your browser-based SSH terminal in Lightsail and run these commands in order.

## Step 1: Check Python Version (Should Already Be Installed)
```bash
python3 --version
# Should show Python 3.x.x
```

## Step 2: Update Server and Install Required Software
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv nginx git
```

## Step 3: Create Application Directory
```bash
sudo mkdir -p /var/www/vdw-server
sudo chown $USER:$USER /var/www/vdw-server
cd /var/www/vdw-server
```

## Step 4: Get Your Code onto the Server

### Option A: Use Git (Easiest)
First, push your code to GitHub from your local machine, then:
```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git .
```

### Option B: Create Files Manually
Create each file using nano:
```bash
nano manage.py
# Paste content, then Ctrl+X, Y, Enter to save
```
Repeat for all project files.

## Step 5: Create Python Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

## Step 6: Install Python Packages
```bash
pip install --upgrade pip
pip install Django==5.2.5
pip install django-markdownx==4.0.9
pip install Markdown==3.8.2
pip install markdown2==2.5.4
pip install pillow==11.3.0
pip install sqlparse==0.5.3
pip install gunicorn
```

## Step 7: Set Up Django for Production
```bash
# Generate a secret key
python3 -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())' > secret.txt

# Create the database
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Create admin user (optional)
python manage.py createsuperuser
```

## Step 8: Create Gunicorn Service File
```bash
sudo nano /etc/systemd/system/gunicorn.service
```

Paste this content:
```
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/var/www/vdw-server
ExecStart=/var/www/vdw-server/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 vdw_server.wsgi:application

[Install]
WantedBy=multi-user.target
```
Save with Ctrl+X, Y, Enter

## Step 9: Start Gunicorn
```bash
sudo systemctl daemon-reload
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
sudo systemctl status gunicorn
```

## Step 10: Configure Nginx
```bash
sudo nano /etc/nginx/sites-available/vdw-server
```

Paste this content:
```
server {
    listen 80;
    server_name _;
    
    location /static/ {
        alias /var/www/vdw-server/static/;
    }
    
    location /media/ {
        alias /var/www/vdw-server/media/;
    }
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```
Save with Ctrl+X, Y, Enter

## Step 11: Enable Nginx Site
```bash
sudo ln -s /etc/nginx/sites-available/vdw-server /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

## Step 12: Test Your Site
1. Go to AWS Lightsail console
2. Find your instance's public IP address
3. Open a browser and go to: http://YOUR_IP_ADDRESS
4. Admin panel: http://YOUR_IP_ADDRESS/admin

## If Something Goes Wrong
Check the logs:
```bash
# Gunicorn logs
sudo journalctl -u gunicorn -n 50

# Nginx logs  
sudo tail -f /var/log/nginx/error.log

# Restart services
sudo systemctl restart gunicorn
sudo systemctl restart nginx
```
