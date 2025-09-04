# VDW Server Docker Deployment Guide

**Simple, automated deployment using Docker containers - no more server configuration nightmares!**

## Prerequisites

1. **AWS EC2 Instance** (Ubuntu 24.04 LTS recommended)
2. **Local .env file** configured with deployment settings
3. **AWS CLI configured** (for security group management)

## Quick Start

### Step 1: Configure Environment Variables

Create/update your `.env` file with these settings:

```bash
# EC2 Instance Configuration
EC2_INSTANCE_ID=i-1234567890abcdef0
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
MEILISEARCH_INDEX_NAME=posts
```

### Step 2: Deploy to Fresh Server

```bash
# Run the deployment script
python deploy-docker.py

# Select Option 0: Provision Server (first time only)
# This will:
# - Configure security groups
# - Install Docker
# - Upload your code
# - Set up environment

# Then select Option 3: Full Deploy
# This will:
# - Deploy your code
# - Upload database
# - Start all services
# - Rebuild search index
```

### Step 3: Access Your Site

Your application will be available at:
- **Main site**: `http://YOUR_EC2_IP:8000`
- **Admin panel**: `http://YOUR_EC2_IP:8000/admin`
- **Meilisearch**: `http://YOUR_EC2_IP:7700`

## Deployment Options

The `deploy-docker.py` script provides these options:

- **0. Provision Server** - Initial server setup (install Docker, upload code, configure)
- **1. Deploy Code** - Upload code and rebuild containers
- **2. Deploy Database** - Upload database and reindex search  
- **3. Full Deploy** - Deploy both code and database
- **4. Reindex Search** - Rebuild search index only
- **5. Show Status** - View container status and logs
- **6. Exit**

## Typical Workflows

### Fresh Deployment
```
Option 0 (Provision Server) → Option 3 (Full Deploy)
```

### Code Updates
```
Option 1 (Deploy Code)
```

### Database Updates
```
Option 2 (Deploy Database)
```

### Both Code and Database Updates
```
Option 3 (Full Deploy)
```

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

## Troubleshooting

### View Container Logs
```bash
python deploy-docker.py
# Select Option 5 (Show Status)
```

### Manual SSH Access
```bash
ssh -i ~/.ssh/your-key.pem ubuntu@YOUR_EC2_IP

# Check containers
cd /app
docker compose ps
docker compose logs django
docker compose logs meilisearch
```

### Common Issues

**Connection Refused**
- Check that security groups include ports 8000 and 7700
- Verify containers are running: `docker compose ps`
- Check Django logs for crashes

**Database Permission Errors**
- Run Option 2 (Deploy Database) to fix permissions
- Verify database file exists and has correct ownership

**Search Not Working**
- Run Option 4 (Reindex Search) to rebuild search index
- Check Meilisearch is accessible at port 7700

## Architecture

- **Django** (port 8000): Main web application
- **Meilisearch** (port 7700): Search engine
- **SQLite**: Database (mounted as Docker volume)
- **AWS S3**: Static file storage

## Security Notes

- Database and .env files are never committed to git
- SSH keys are required for server access
- Security groups restrict access to necessary ports only
- All services run in isolated Docker containers

## File Structure

```
/app/                    # Application root on server
├── docker-compose.yml   # Container orchestration
├── Dockerfile          # Django container definition
├── .env                # Environment variables (uploaded)
├── db.sqlite3          # Database file (uploaded)
├── manage.py           # Django management
├── requirements.txt    # Python dependencies
└── ...                # Application code
```

This Docker-based approach eliminates server configuration complexity while providing consistent, reproducible deployments.