# Automated Deployment Script Plan

## Overview
Create a Python script that automates deployment tasks via SSH, providing a menu-driven interface for different deployment scenarios.

## Deployment Options

### 1. Full Deploy (From Scratch) - NOT IMPLEMENTED YET
- **Status**: Placeholder only, throws NotImplementedError
- **Future scope**:
  - Provision fresh server
  - Install system dependencies (Python, nginx, etc.)
  - Clone repository
  - Setup virtual environment
  - Install Python dependencies
  - Configure systemd services
  - Setup nginx configuration
  - Initialize database
  - Configure environment variables

### 2. Update Code
- **Purpose**: Pull latest code from GitHub and restart services
- **Steps**:
  1. SSH into server
  2. Navigate to project directory
  3. Git pull latest changes from main branch
  4. Restart Django application service (systemd/supervisor)
  5. Verify service is running
  6. Optional: Run collectstatic for static files
  
### 3. Update Database
- **Purpose**: Upload local SQLite database to server and restart
- **Steps**:
  1. Create backup of current server database
  2. SCP/SFTP local db.sqlite3 to server
  3. Set appropriate permissions on database file
  4. Restart Django application service
  5. Verify service is running
  6. Optional: Clear and rebuild search index

## Script Structure

### Main Components

```python
deploy.py
├── Configuration Section
│   ├── Server details (host, user, port)
│   ├── Project paths (local and remote)
│   └── Service names
├── SSH Connection Manager
│   ├── Connection establishment
│   ├── Command execution
│   └── File transfer (SCP/SFTP)
├── Deployment Functions
│   ├── deploy_full() - NotImplementedError
│   ├── deploy_code_update()
│   └── deploy_database_update()
├── Utility Functions
│   ├── create_backup()
│   ├── verify_service_status()
│   └── rollback_on_error()
└── Main Menu Interface
    └── Interactive option selection
```

### User Interface

```
========================================
    VDW Server Deployment Script
========================================

Select deployment option:

1. Full Deploy (from scratch) - NOT IMPLEMENTED
2. Update Code (pull from GitHub)
3. Update Database (upload local SQLite)
4. Exit

Enter choice [1-4]: 
```

## Configuration Management

### Environment Variables (.env file)
All configuration stored in `.env` file (gitignored) for consistency with rest of project.

### Required .env variables:
```bash
# SSH Configuration
DEPLOY_HOST=your-server.com
DEPLOY_USER=deploy
DEPLOY_PORT=22
DEPLOY_KEY_FILE=~/.ssh/id_rsa  # Optional, uses system default if not set

# Remote Paths
DEPLOY_REMOTE_PROJECT=/var/www/vdw-server
DEPLOY_REMOTE_VENV=/var/www/vdw-server/venv
DEPLOY_BACKUP_DIR=/var/backups/vdw-server

# Local Paths
DEPLOY_LOCAL_DB=./db.sqlite3

# Service Names
DEPLOY_APP_SERVICE=vdw-server  # systemd service name
DEPLOY_WEB_SERVER=nginx

# Optional
DEPLOY_BRANCH=main  # Git branch to pull from
DEPLOY_PYTHON_BIN=python3  # Python binary on server
```

### Loading configuration:
```python
from dotenv import load_dotenv
import os

load_dotenv()

CONFIG = {
    'server': {
        'host': os.getenv('DEPLOY_HOST'),
        'user': os.getenv('DEPLOY_USER'),
        'port': int(os.getenv('DEPLOY_PORT', 22)),
        'key_file': os.getenv('DEPLOY_KEY_FILE')  # None if not set
    },
    'paths': {
        'remote_project': os.getenv('DEPLOY_REMOTE_PROJECT'),
        'remote_venv': os.getenv('DEPLOY_REMOTE_VENV'),
        'local_db': os.getenv('DEPLOY_LOCAL_DB', './db.sqlite3'),
        'backup_dir': os.getenv('DEPLOY_BACKUP_DIR')
    },
    'services': {
        'app_service': os.getenv('DEPLOY_APP_SERVICE'),
        'web_server': os.getenv('DEPLOY_WEB_SERVER', 'nginx')
    },
    'git': {
        'branch': os.getenv('DEPLOY_BRANCH', 'main')
    }
}
```

## Error Handling

### Rollback Strategy
- For code updates: Git reset to previous commit
- For database updates: Restore from backup
- Always verify service status after operations
- Log all operations with timestamps

### Safety Checks
- Confirm destructive operations
- Verify SSH connection before operations
- Check disk space before database upload
- Verify git status before pull

## Dependencies

### Python Packages
- `paramiko` - SSH connections and commands
- `scp` or `paramiko.SFTPClient` - File transfers
- `click` or `argparse` - CLI interface (optional)
- `colorama` or `rich` - Colored output (optional)

### System Requirements
- SSH access to server
- Sudo privileges for service restart (or appropriate permissions)
- Git configured on server
- Python 3.x on both local and server

## Testing Plan

### Local Testing
1. Test SSH connection establishment
2. Test command execution (non-destructive commands)
3. Test file transfer with small test file
4. Verify menu system and input validation

### Staging Testing
1. Test on staging server first if available
2. Test rollback procedures
3. Verify service restart commands
4. Test database backup and restore

### Production Deployment
1. Always backup before deployment
2. Test in low-traffic period initially
3. Monitor logs after deployment
4. Have rollback plan ready

## Future Enhancements

### Phase 1 (Current)
- Basic menu-driven deployment
- Code and database updates
- Simple error handling

### Phase 2
- Implement full deployment from scratch
- Add health checks (HTTP endpoint verification)
- Automated backup retention policy
- Deployment history logging

### Phase 3
- Multiple environment support (staging, production)
- Blue-green deployment option
- Database migration management
- Automated rollback on health check failure
- Integration with CI/CD pipeline

## Security Considerations

- Never commit sensitive credentials
- Use SSH keys instead of passwords
- Implement confirmation prompts for production
- Log all deployment activities
- Restrict deployment script access
- Consider using deployment user with limited privileges