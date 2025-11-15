# VDW Server Deployment Guide

**Automated deployment using Docker containers via the deployment-manager.py script**

## Prerequisites

1. **AWS EC2 Instance** (Ubuntu 24.04 LTS recommended)
2. **Local .env file** configured with deployment settings
3. **AWS CLI configured** (for provisioning and security group management)
4. **Python 3** with required packages: `paramiko`, `scp`, `python-dotenv`

## Quick Start

### Step 1: Install Dependencies

```bash
pip install paramiko scp python-dotenv
```

### Step 2: Configure Environment Variables

Create/update your `.env` file with these settings:

```bash
# EC2 Instance Configuration
EC2_INSTANCE_ID=i-1234567890abcdef0  # Required only for provisioning
DEPLOY_HOST=your-ec2-public-ip
DEPLOY_USER=ubuntu
DEPLOY_PORT=22
DEPLOY_KEY_FILE=~/.ssh/your-key.pem
DEPLOY_APP_PATH=/app
DEPLOY_LOCAL_DB=./db.sqlite3
DJANGO_PORT=8000

# AWS Configuration (for S3 storage)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_DEFAULT_REGION=us-west-2
AWS_STORAGE_BUCKET_NAME=your-bucket

# Meilisearch Configuration
MEILISEARCH_URL=http://localhost:7700
MEILISEARCH_MASTER_KEY=your_master_key
MEILISEARCH_INDEX_NAME=pages

# Search Presentation
SEARCH_RESULTS_DISPLAY_MODE=full  # Options: full, title_only
```

### Step 3: Deploy to Server

```bash
# Run the deployment manager
python deployment-manager.py
```

## Deployment Options

The deployment-manager.py script provides an interactive menu with these options:

### 0. Provision Server (Initial Setup)
- Waits for EC2 instance to be running
- Configures security group ports (Django + Meilisearch)
- Installs Docker and Docker Compose
- Uploads application code from local machine
- Sets up environment variables

**Use this for:** First-time server setup

### 1. Deploy Code
- Uploads fresh code from local machine via SCP
- Rebuilds Docker containers with new code
- Restarts all services

**Use this for:** Deploying code changes only

### 2. Deploy Database
- Uploads local database file to server
- Stops Django container to avoid database locks
- Sets proper permissions for SQLite
- Restarts Django container
- Rebuilds search index

**Use this for:** Updating database content

### 3. Full Deploy
- Performs both code and database deployment
- Uploads code, rebuilds containers
- Uploads database, rebuilds search index

**Use this for:** Complete application updates

### 4. Reindex Search
- Rebuilds the Meilisearch index
- No code or database changes

**Use this for:** Fixing search issues

### 5. Troubleshoot Server / Show Status
- Shows Docker container status
- Displays recent container logs (last 20 lines)

**Use this for:** Debugging deployment issues

### 6. Exit
- Exits the deployment manager

## Typical Workflows

### Fresh Server Deployment
```
1. Configure .env with EC2_INSTANCE_ID and other settings
2. Run: python deployment-manager.py
3. Select: Option 0 (Provision Server)
4. Wait ~30 seconds for Docker group changes
5. Select: Option 3 (Full Deploy)
```

### Code Updates Only
```
1. Make code changes locally
2. Run: python deployment-manager.py
3. Select: Option 1 (Deploy Code)
```

### Database Updates Only
```
1. Update local database
2. Run: python deployment-manager.py
3. Select: Option 2 (Deploy Database)
```

### Complete Application Update
```
1. Make code changes and database updates
2. Run: python deployment-manager.py
3. Select: Option 3 (Full Deploy)
```

## How It Works

### Code Deployment Process
1. Connects to server via SSH
2. Uploads all Python files, requirements, Docker configs
3. Uploads application directories (pages, templates, static, etc.)
4. Excludes: .git, __pycache__, .env, db.sqlite3, venv
5. Rebuilds and restarts Docker containers
6. Docker Compose mounts host directory `/app/data` into the container; SQLite DB path is `/app/data/db.sqlite3`

### Database Deployment Process
1. Stops Django container to release database lock
2. Ensures `/app/data` exists and uploads local SQLite database to `/app/data/db.sqlite3`
3. Sets root:root ownership and 644 permissions (required for Docker)
4. Restarts Django container
5. Rebuilds search index automatically

### Security Features
- Uses SSH key authentication
- Never uploads .env or sensitive files to git
- Configures AWS security groups for required ports only
- Sets proper file permissions for Docker containers

## Access Points

After deployment, your application is available at:
- **Main site**: `http://YOUR_EC2_IP:8000`
- **Admin panel**: `http://YOUR_EC2_IP:8000/admin`
- **Meilisearch**: `http://YOUR_EC2_IP:7700`

## Troubleshooting

### Connection Issues
```bash
# Check deployment status
python deployment-manager.py
# Select Option 5 (Show Status)
```

### Manual SSH Access
```bash
ssh -i ~/.ssh/your-key.pem ubuntu@YOUR_EC2_IP

# Check containers
cd /app
docker compose ps
docker compose logs --tail=50 django
docker compose logs --tail=50 meilisearch
```

### Common Issues

**SSH Connection Failed**
- Verify DEPLOY_HOST is correct in .env
- Check DEPLOY_KEY_FILE path is valid
- Ensure EC2 instance is running

**Port Connection Refused**
- Run provisioning to configure security groups
- Verify containers are running with Option 5
- Check firewall settings on EC2

**Database Permission Errors**
- Run Option 2 to re-deploy database with correct permissions
- Database should be owned by root:root with 644 permissions at `/app/data/db.sqlite3`

**Search Not Working**
- Run Option 4 to rebuild search index
- Check Meilisearch container is running
- Verify MEILISEARCH_URL in .env

**Code Changes Not Reflected**
- Ensure you're uploading from correct local directory
- Check Docker rebuild completed successfully
- Clear browser cache

## Development Workflows

For local development, use the `dev.py` script:

### Hybrid Development (PyCharm Debugging)
```bash
# Start only Meilisearch in Docker
python dev.py venv-meilisearch

# Run Django in your local venv for debugging
python manage.py runserver
```

### Full Docker Development
```bash
# Start complete stack
python dev.py docker-build

# View logs
python dev.py docker-logs

# Stop everything
python dev.py docker-stop
```

## Architecture

- **Django** (port 8000): Main web application
- **Meilisearch** (port 7700): Search engine
- **SQLite**: Database (mounted as Docker volume)
- **AWS S3**: Static file storage (optional)
- **Docker Compose**: Container orchestration

## File Structure

```
Local Machine:
├── deployment-manager.py  # Deployment automation script
├── .env                  # Environment configuration
├── db.sqlite3           # Local database
└── [application code]   # Your Django application

Server (/app):
├── docker-compose.yml   # Container orchestration
├── Dockerfile          # Django container definition
├── .env                # Environment variables (uploaded)
├── db.sqlite3          # Database file (uploaded)
├── manage.py           # Django management
├── requirements.txt    # Python dependencies
└── [application code]  # Uploaded from local machine
```

## Security Notes

- Database and .env files are excluded from git
- SSH key authentication required for deployment
- Security groups automatically configured for required ports
- All services run in isolated Docker containers
- Database permissions set for Docker compatibility

This automated deployment approach eliminates manual server configuration while providing consistent, reproducible deployments across environments.
