# Docker Deployment Plan

## Overview
Move from the Bitnami/systemd nightmare to a clean Docker-based deployment that:
- Runs consistently in development (venv OR docker) and production (docker)
- Eliminates server permission/configuration hell
- Provides clean deployment automation
- Supports operational tasks (database updates, search reindexing)

## Phase 1: Local Docker Setup

### 1.1 Development Environment Support
**Goal**: App works in both environments for maximum flexibility
- **PyCharm debugging**: Run in venv with local Meilisearch
- **Production parity**: Run in Docker containers

**Implementation**:
- Environment detection in settings
- Docker compose for full stack
- Documentation for both workflows

### 1.2 Management Commands
Add operational commands to manage.py:
```python
python manage.py reindex_search  # Clear and rebuild Meilisearch index
```

### 1.3 Docker Configuration
**Dockerfile**:
- Python 3.12 base image
- Install dependencies from requirements.txt
- Copy application code
- Expose port 8000

**docker-compose.yml**:
- Django app service
- Meilisearch service (with master key)
- Volume mounts for:
  - Database file (persistent)
  - Static files
  - Media files (if any)

### 1.4 Environment Configuration
**.env structure**:
```
# Database
DATABASE_URL=sqlite:///db.sqlite3

# Meilisearch
MEILISEARCH_URL=http://meilisearch:7700  # Docker service name
MEILISEARCH_MASTER_KEY=your-key
MEILISEARCH_INDEX_NAME=posts

# Development overrides
MEILISEARCH_URL=http://localhost:7700    # When running in venv
```

## Phase 2: Production Deployment

### 2.1 Platform Choice
**Target**: EC2 instance with plain Ubuntu (no Bitnami)
- Clean permissions
- Standard Docker installation
- Predictable environment

### 2.2 Deployment Workflow
**Code Updates**:
```bash
# On EC2 instance
git pull
docker-compose up --build -d
```

**Database Updates**:
```bash
# Local to EC2
scp ./db.sqlite3 user@server:/app/db.sqlite3

# On EC2
docker-compose stop django
docker-compose start django
docker-compose exec django python manage.py reindex_search
```

### 2.3 Deployment Script
Create `deploy.py` (Docker version):
- **Option 1**: Deploy code (git pull + rebuild)
- **Option 2**: Deploy database (scp + reindex)  
- **Option 3**: Full deploy (both)
- **Option 4**: Reindex search only
- **Option 5**: View logs/status

### 2.4 Server Setup (One-time)
```bash
# EC2 Ubuntu setup
sudo apt update
sudo apt install docker.io docker-compose git
sudo usermod -aG docker ubuntu

# Clone repo and set up
git clone your-repo /app
cd /app
cp .env.example .env  # Edit with production values
docker-compose up -d
```

## Phase 3: Operational Benefits

### 3.1 Eliminated Problems
- ❌ Permission conflicts between bitnami/www-data
- ❌ systemd service configuration
- ❌ Manual dependency management  
- ❌ Server-specific environment issues
- ❌ SSH debugging sessions

### 3.2 New Capabilities
- ✅ Consistent dev/prod environments
- ✅ Easy rollbacks (`git checkout previous-commit && docker-compose up --build`)
- ✅ Isolated services (Django, Meilisearch)
- ✅ Simple scaling (add more containers)
- ✅ Clear deployment process

### 3.3 Development Workflow
**Daily development** (PyCharm):
```bash
# Start Meilisearch only
docker-compose up meilisearch -d
# Run Django in venv for debugging
python manage.py runserver
```

**Integration testing** (Docker):
```bash
# Full stack
docker-compose up --build
```

**Production deployment**:
```bash
# Test locally first
docker-compose up --build
# Then deploy to server
./deploy.py  # Option 1: Deploy code
```

## Implementation Order
1. ✅ Create management command for search reindexing
2. ✅ Create Dockerfile
3. ✅ Create docker-compose.yml  
4. ✅ Test hybrid development setup
5. ✅ Create deployment script
6. ✅ Test full workflow locally
7. ✅ Deploy to clean EC2 instance
8. ✅ Document the process

## Success Criteria
- App runs identically in venv and Docker
- Deployment is single command
- Database updates work reliably
- Search reindexing works correctly
- No more SSH debugging sessions
- Server crashes don't require complex recovery