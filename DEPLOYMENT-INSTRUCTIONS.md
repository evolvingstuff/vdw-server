# VDW Server Deployment Guide

**Automated deployment using Docker containers via the deployment-manager.py script**

## Prerequisites

1. **AWS account + credentials** with permission to manage EC2 instances, security groups, EBS volumes, and the pre-allocated Elastic IP you plan to reuse.
2. **Local `.env` file** that includes both deployment settings (host/user/etc.) and the provisioning variables listed below.
3. **Python 3 environment** with the required packages (`paramiko`, `scp`, `python-dotenv`, `boto3`). Installing via `pip install -r requirements.txt` also works.
4. **Elastic IP** already allocated in AWS. The provisioning workflow will swap this IP later, but it will never create a new one so DNS stays predictable.

## Quick Start

### Step 1: Install Dependencies

```bash
pip install paramiko scp python-dotenv boto3
```

### Step 2: Configure Environment Variables

Create/update your `.env` file with these settings:

```bash
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

### Step 3: Capture Provisioning Config (one time)

Run the deployment manager and choose **Option 0** to import settings from your current production instance. The tool asks for minimal input (region, Elastic IP host, desired instance type / disk sizes) and writes everything else it can detect to `config/provisioning.json`. This JSON file is version-controlled so future runs already know which subnet, VPC, security group, AMI, etc. to reuse.

Example `config/provisioning.json` (placeholder values):

```json
{
  "aws_region": "us-east-1",
  "aws_profile": "",
  "instance_type": "t3.small",
  "ami_id": "ami-0123456789abcdef0",
  "subnet_id": "subnet-0123456789abcdef0",
  "vpc_id": "vpc-0123456789abcdef0",
  "security_group_id": "sg-0123456789abcdef0",
  "key_name": "vdw-prod-keypair",
  "root_volume_gb": 40,
  "data_volume_gb": 100,
  "root_device_name": "/dev/sda1",
  "data_device_name": "/dev/sdf",
  "ssh_ingress_cidr": "0.0.0.0/0",
  "extra_ports": [8000, 7700],
  "elastic_ip_allocation_id": "eipalloc-0123456789abcdef0"
}
```

Edit this file directly if you ever need to change defaults (AMI, sizes, etc.).
You never need to add a `Name=` tag here—the provisioner automatically tags each instance as `vdw<YYYYMMDDHHMMSS>` (UTC timestamp) so every server is clearly labeled.

When the CLI starts it targets the Elastic IP (`elastic_hostname` / `elastic_ip_address`). After you provision, it automatically switches the active host to the new instance’s temporary IP so you can finish prep work before swapping the Elastic IP. Option 9 lets you toggle between the production host and the latest provisioned host at any time.

### Step 4: Deploy to Server

```bash
# Run the deployment manager
python deployment-manager.py
```

## Deployment Options

`deployment-manager.py` now offers nine options:

### 0. Capture Provisioning Config
- Reads your current production instance (via Elastic IP) and writes `config/provisioning.json` so future provisioning runs know which subnet/VPC/AMI/security group/key pair/etc. to reuse.

### 1. Provision + Bootstrap New Server (Phase 1)
- Creates a fresh EC2 instance (values driven by `config/provisioning.json`)
- Ensures security group rules for SSH/HTTP/HTTPS/Django/Meilisearch
- Installs Docker, docker compose, nginx, and (optionally) mounts a dedicated `/app/data` volume
- Uploads the current codebase + `.env`, builds containers, and leaves the app running on the instance’s temporary public IP (Elastic IP not yet reassigned)

### 2. Associate Elastic IP
- Moves the configured Elastic IP allocation to any instance ID (defaults to the most recent provisioning run stored in `tmp/provision-state.json`)
- Prompts before reassigning and optionally terminates the previous instance afterward

### 3. Deploy Code
- Uploads fresh code from your local working copy
- Rebuilds Docker images, restarts containers, and runs Django migrations

### 4. Deploy Database
- Uploads the local SQLite database to `/app/data/db.sqlite3`
- Stops the Django container during copy, fixes permissions, and rebuilds the search index

### 5. Full Deploy
- Runs option 3 followed by option 4 in one flow (code + database)

### 6. Reindex Search
- Rebuilds the Meilisearch index without touching code or the DB

### 7. Free Disk on Server (Dangerous)
- Stops all containers, deletes the remote SQLite DB + Meilisearch volume, prunes Docker caches/logs, and frees disk space so you can upload a clean database.

### 8. Troubleshoot / Show Status
- Shows `docker compose ps` plus the last 20 log lines from every container

### 9. Switch Active Host
- Toggle between the production Elastic-IP host and the latest provisioned (temporary) host for subsequent commands.

### 10. Exit
- Leaves the tool

## Typical Workflows

### Fresh Server Deployment (Phase 1 + Cutover)
```
1. (First time only) Run option 0 to capture provisioning config from the current instance
2. Run: python deployment-manager.py
3. Select: Option 1 (Provision + Bootstrap)
4. Test the site via the temporary public IP (optionally add it to /etc/hosts)
5. When ready, select Option 2 (Associate Elastic IP) to move DNS traffic to the new box
6. Optionally terminate the previous instance when prompted
```

### Code Updates Only
```
1. Make code changes locally
2. Run: python deployment-manager.py
3. Select: Option 3 (Deploy Code)
```

### Database Updates Only
```
1. Update the local database file
2. Run: python deployment-manager.py
3. Select: Option 4 (Deploy Database)
```

### Complete Application Update
```
1. Make code + database changes locally
2. Run: python deployment-manager.py
3. Select: Option 5 (Full Deploy)
```

## How It Works

### Provisioning Config File
- Stored at `config/provisioning.json` and version-controlled
- Created automatically via option 0 (or by editing manually)
- Captures immutable infrastructure identifiers (AMI, subnet, VPC, security group, key pair, Elastic IP allocation) plus tunables like instance type and disk sizes
- Read by option 1 so provisioning never prompts for those details again

### Phase 1 – Provision + Bootstrap
1. Use boto3 to create (or reuse) the configured security group; opens ports 22/80/443 plus Django/Meilisearch/extra ports.
2. Launch a new Ubuntu 24.04 instance with the configured AMI, instance type, block device sizes, IAM profile, and tags.
3. Wait for EC2 + system checks, then poll for SSH availability on the temporary public IP.
4. SSH in and automate bootstrap tasks: install Docker/docker compose, install nginx, format/mount the optional `/app/data` EBS volume, upload the repository + `.env`, configure nginx as a reverse proxy, and build the Docker stack.
5. Persist metadata (instance ID, IPs, security group, volume IDs) to `tmp/provision-state.json` so the Elastic IP workflow knows which instance to target.
6. Leave the Elastic IP untouched so you can test via the temporary IP before flipping DNS.

### Provisioning State File
- Located at `tmp/provision-state.json` whenever option 0 finishes.
- Stores the newest instance ID, temporary public IP, private IP, security group ID, and any mounted data volume ID.
- Option 2 (Associate Elastic IP) uses this file to pre-fill the instance prompt, so you rarely need to paste IDs manually.

### Code Deployment Process
1. Connects to the current deployment target via SSH
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
# Select Option 8 (Show Status)
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
- Verify containers are running with Option 8
- Check firewall settings on EC2

**Database Permission Errors**
- Run Option 4 to re-deploy database with correct permissions
- Database should be owned by root:root with 644 permissions at `/app/data/db.sqlite3`

**Search Not Working**
- Run Option 6 to rebuild search index
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
