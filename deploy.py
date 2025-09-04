#!/usr/bin/env python3
"""
Deployment script for VDW Server
Automates deployment tasks via SSH with menu-driven interface
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import paramiko
from scp import SCPClient

# Load environment variables
load_dotenv()

# Configuration from .env
CONFIG = {
    'server': {
        'host': os.getenv('DEPLOY_HOST'),
        'user': os.getenv('DEPLOY_USER', 'bitnami'),
        'port': int(os.getenv('DEPLOY_PORT', 22)),
        'key_file': os.getenv('DEPLOY_KEY_FILE'),  # Path to SSH key
        'password': os.getenv('DEPLOY_PASSWORD')   # Optional if using password
    },
    'paths': {
        'remote_project': os.getenv('DEPLOY_REMOTE_PROJECT', '/opt/bitnami/apache/htdocs/django-app'),
        'remote_venv': os.getenv('DEPLOY_REMOTE_VENV', '/opt/bitnami/apache/htdocs/django-app/venv'),
        'local_db': os.getenv('DEPLOY_LOCAL_DB', './db.sqlite3'),
        'backup_dir': os.getenv('DEPLOY_BACKUP_DIR', '/opt/bitnami/apache/htdocs/django-app')
    },
    'services': {
        'app_service': os.getenv('DEPLOY_APP_SERVICE'),  # systemd service if using
        'web_server': os.getenv('DEPLOY_WEB_SERVER', 'apache'),
        'control_script': os.getenv('DEPLOY_CONTROL_SCRIPT', '/opt/bitnami/ctlscript.sh')
    },
    'git': {
        'branch': os.getenv('DEPLOY_BRANCH', 'main')
    }
}


class DeploymentManager:
    """Manages deployment operations via SSH"""
    
    def __init__(self):
        self.ssh_client = None
        self.validate_config()
    
    def validate_config(self):
        """Validate required configuration"""
        if not CONFIG['server']['host']:
            raise ValueError("DEPLOY_HOST not set in .env file")
        
        if not CONFIG['server']['key_file'] and not CONFIG['server']['password']:
            raise ValueError("Either DEPLOY_KEY_FILE or DEPLOY_PASSWORD must be set in .env")
    
    def connect(self):
        """Establish SSH connection"""
        print(f"🔌 Connecting to {CONFIG['server']['host']}...")
        
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            connect_kwargs = {
                'hostname': CONFIG['server']['host'],
                'port': CONFIG['server']['port'],
                'username': CONFIG['server']['user']
            }
            
            # Use SSH key if available, otherwise password
            if CONFIG['server']['key_file']:
                key_path = os.path.expanduser(CONFIG['server']['key_file'])
                connect_kwargs['key_filename'] = key_path
            elif CONFIG['server']['password']:
                connect_kwargs['password'] = CONFIG['server']['password']
            
            self.ssh_client.connect(**connect_kwargs)
            print("✅ Connected successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def execute_command(self, command, show_output=True):
        """Execute command on remote server"""
        if not self.ssh_client:
            raise RuntimeError("Not connected to server")
        
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        
        # Read output
        output = stdout.read().decode()
        error = stderr.read().decode()
        
        if show_output and output:
            print(output)
        
        if error:
            print(f"⚠️  {error}")
        
        return stdout.channel.recv_exit_status(), output, error
    
    def deploy_full(self):
        """Full deployment from scratch - NOT IMPLEMENTED"""
        raise NotImplementedError(
            "Full deployment from scratch is not implemented yet.\n"
            "Please use option 2 (Update Code) or 3 (Update Database) instead."
        )
    
    def deploy_code_update(self):
        """Update code from GitHub and restart services"""
        print("\n🚀 Starting code deployment...")
        
        if not self.connect():
            return False
        
        try:
            project_path = CONFIG['paths']['remote_project']
            
            # Navigate to project directory
            print(f"\n📁 Navigating to {project_path}...")
            status, _, _ = self.execute_command(f"cd {project_path} && pwd")
            if status != 0:
                raise RuntimeError(f"Failed to navigate to project directory")
            
            # Handle Git safe directory issue for both bitnami and root users
            print("\n🔧 Configuring Git safe directory...")
            self.execute_command(
                f"git config --global --add safe.directory {project_path}",
                show_output=False
            )
            self.execute_command(
                f"sudo git config --global --add safe.directory {project_path}",
                show_output=False
            )
            
            # Fix Git directory permissions
            print("\n🔧 Fixing Git directory permissions...")
            self.execute_command(
                f"sudo chown -R {CONFIG['server']['user']}:{CONFIG['server']['user']} {project_path}/.git",
                show_output=False
            )
            
            # Check Git remote configuration
            print("\n🔍 Checking Git configuration...")
            status, output, error = self.execute_command(
                f"cd {project_path} && git remote -v"
            )
            
            # Clean up .pyc files that shouldn't be in Git
            print("\n🧹 Cleaning up Python cache files...")
            self.execute_command(
                f"cd {project_path} && sudo find . -type f -name '*.pyc' -delete",
                show_output=False
            )
            self.execute_command(
                f"cd {project_path} && sudo find . -type d -name '__pycache__' -exec rm -rf {{}} + 2>/dev/null || true",
                show_output=False
            )
            
            # Reset any local changes using sudo
            print("\n🔄 Resetting repository to clean state...")
            self.execute_command(
                f"cd {project_path} && sudo git reset --hard HEAD",
                show_output=True
            )
            
            # Git pull latest changes using sudo
            print(f"\n📥 Pulling latest code from {CONFIG['git']['branch']} branch...")
            status, output, error = self.execute_command(
                f"cd {project_path} && sudo git pull origin {CONFIG['git']['branch']}"
            )
            if status != 0:
                raise RuntimeError(f"Git pull failed: {error}")
            
            # Activate virtual environment and install dependencies
            print("\n📦 Installing/updating dependencies...")
            venv_path = CONFIG['paths']['remote_venv']
            status, _, _ = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && pip install -r requirements.txt"
            )
            if status != 0:
                print("⚠️  Warning: Failed to update dependencies")
            
            # Run migrations
            print("\n🗄️  Running database migrations...")
            status, output, _ = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && python manage.py migrate"
            )
            if status != 0:
                print("⚠️  Warning: Migrations may have failed")
            
            # Collect static files
            print("\n📁 Collecting static files...")
            status, _, _ = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && python manage.py collectstatic --noinput"
            )
            
            # Set permissions (from deploy.sh)
            print("\n🔒 Setting permissions...")
            self.execute_command(f"sudo chown -R www-data:www-data {project_path}", show_output=False)
            self.execute_command(f"sudo chmod -R 775 {project_path}", show_output=False)
            self.execute_command(f"sudo chmod 664 {project_path}/db.sqlite3", show_output=False)
            
            # Restart services
            self.restart_services()
            
            print("\n✅ Code deployment completed successfully!")
            return True
            
        except Exception as e:
            print(f"\n❌ Deployment failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def deploy_database_update(self):
        """Upload local database to server"""
        print("\n🗄️  Starting database deployment...")
        
        # Check if local database exists
        local_db = CONFIG['paths']['local_db']
        if not Path(local_db).exists():
            print(f"❌ Local database not found: {local_db}")
            return False
        
        if not self.connect():
            return False
        
        try:
            project_path = CONFIG['paths']['remote_project']
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Create backup of current database
            print("\n💾 Backing up current database...")
            backup_file = f"db.sqlite3.backup.{timestamp}"
            status, _, _ = self.execute_command(
                f"cd {project_path} && [ -f db.sqlite3 ] && cp db.sqlite3 {backup_file}"
            )
            if status == 0:
                print(f"✅ Backup created: {backup_file}")
            
            # Upload new database
            print(f"\n📤 Uploading database from {local_db}...")
            with SCPClient(self.ssh_client.get_transport()) as scp:
                remote_db_path = f"{project_path}/db.sqlite3"
                scp.put(local_db, remote_db_path)
            print("✅ Database uploaded successfully!")
            
            # Set permissions
            print("\n🔒 Setting database permissions...")
            self.execute_command(f"sudo chown www-data:www-data {project_path}/db.sqlite3")
            self.execute_command(f"sudo chmod 664 {project_path}/db.sqlite3")
            
            # Restart services
            self.restart_services()
            
            # Optionally rebuild search index
            if input("\n🔍 Rebuild search index? (y/n): ").lower() == 'y':
                print("🔍 Rebuilding search index...")
                venv_path = CONFIG['paths']['remote_venv']
                self.execute_command(
                    f"cd {project_path} && source {venv_path}/bin/activate && "
                    f"python -c \"from search.search import clear_search_index, initialize_search_index, bulk_index_posts; "
                    f"from posts.models import Post; "
                    f"clear_search_index(); initialize_search_index(); "
                    f"bulk_index_posts(Post.objects.filter(status='published'))\""
                )
                print("✅ Search index rebuilt!")
            
            print("\n✅ Database deployment completed successfully!")
            return True
            
        except Exception as e:
            print(f"\n❌ Database deployment failed: {e}")
            # Offer to restore from backup
            if input("\n⚠️  Restore from backup? (y/n): ").lower() == 'y':
                print("🔄 Restoring from backup...")
                self.execute_command(
                    f"cd {project_path} && cp {backup_file} db.sqlite3"
                )
                self.restart_services()
                print("✅ Restored from backup")
            return False
        
        finally:
            self.disconnect()
    
    def restart_services(self):
        """Restart web services"""
        print("\n🔄 Restarting services...")
        
        # If using systemd service
        if CONFIG['services']['app_service']:
            status, _, _ = self.execute_command(
                f"sudo systemctl restart {CONFIG['services']['app_service']}"
            )
            if status == 0:
                print(f"✅ {CONFIG['services']['app_service']} restarted")
        
        # If using Bitnami control script (from deploy.sh)
        if CONFIG['services']['control_script']:
            status, _, _ = self.execute_command(
                f"sudo {CONFIG['services']['control_script']} restart {CONFIG['services']['web_server']}"
            )
            if status == 0:
                print(f"✅ {CONFIG['services']['web_server']} restarted")
    
    def disconnect(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print("🔌 Disconnected from server")


def print_header():
    """Print script header"""
    print("\n" + "=" * 50)
    print("    VDW Server Deployment Script")
    print("=" * 50)


def print_menu():
    """Print deployment menu"""
    print("\nSelect deployment option:\n")
    print("1. Full Deploy (from scratch) - NOT IMPLEMENTED")
    print("2. Update Code (pull from GitHub)")
    print("3. Update Database (upload local SQLite)")
    print("4. Exit")
    print()


def main():
    """Main entry point"""
    print_header()
    
    # Create deployment manager
    try:
        deployer = DeploymentManager()
    except ValueError as e:
        print(f"\n❌ Configuration error: {e}")
        print("\nPlease ensure your .env file contains:")
        print("  DEPLOY_HOST=your-server.com")
        print("  DEPLOY_USER=bitnami")
        print("  DEPLOY_KEY_FILE=~/.ssh/your-key.pem")
        print("\nOr check .env.example for all available options.")
        sys.exit(1)
    
    while True:
        print_menu()
        choice = input("Enter choice [1-4]: ").strip()
        
        if choice == '1':
            try:
                deployer.deploy_full()
            except NotImplementedError as e:
                print(f"\n⚠️  {e}")
        
        elif choice == '2':
            print("\n" + "=" * 50)
            print("CODE UPDATE DEPLOYMENT")
            print("=" * 50)
            
            # Show what will be done
            print("\nThis will:")
            print("  • Pull latest code from GitHub")
            print("  • Install/update dependencies")
            print("  • Run database migrations")
            print("  • Collect static files")
            print("  • Restart services")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.deploy_code_update()
        
        elif choice == '3':
            print("\n" + "=" * 50)
            print("DATABASE UPDATE DEPLOYMENT")
            print("=" * 50)
            
            # Check local database
            local_db = CONFIG['paths']['local_db']
            if Path(local_db).exists():
                db_size = Path(local_db).stat().st_size / (1024 * 1024)  # MB
                print(f"\nLocal database: {local_db} ({db_size:.1f} MB)")
            else:
                print(f"\n❌ Local database not found: {local_db}")
                continue
            
            print("\nThis will:")
            print("  • Backup current server database")
            print("  • Upload local database to server")
            print("  • Set appropriate permissions")
            print("  • Restart services")
            print("  • Optionally rebuild search index")
            
            if input("\n⚠️  This will replace the server database! Proceed? (y/n): ").lower() == 'y':
                deployer.deploy_database_update()
        
        elif choice == '4':
            print("\n👋 Exiting deployment script")
            break
        
        else:
            print("\n❌ Invalid choice. Please enter 1-4.")
    
    sys.exit(0)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Deployment interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)