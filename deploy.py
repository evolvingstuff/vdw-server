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
        'local_env': os.getenv('DEPLOY_LOCAL_ENV', './.env'),
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
            "Please use other options instead."
        )
    
    def install_meilisearch(self):
        """Install and configure Meilisearch on the server"""
        print("\n🔍 Starting Meilisearch installation...")
        
        if not self.connect():
            return False
        
        try:
            # Check if Meilisearch is already installed
            print("\n📋 Checking if Meilisearch is already installed...")
            status, output, _ = self.execute_command("which meilisearch")
            if status == 0:
                print("✅ Meilisearch is already installed at:", output.strip())
                if input("\n🔄 Reinstall/Update Meilisearch? (y/n): ").lower() != 'y':
                    return True
            
            # Install Meilisearch
            print("\n📦 Installing Meilisearch...")
            
            # Download and install latest Meilisearch
            commands = [
                # Download Meilisearch binary
                "curl -L https://install.meilisearch.com | sh",
                
                # Move to system location
                "sudo mv ./meilisearch /usr/local/bin/",
                
                # Make it executable
                "sudo chmod +x /usr/local/bin/meilisearch",
                
                # Create data directory with proper permissions
                "sudo mkdir -p /var/lib/meilisearch",
                "sudo chown bitnami:bitnami /var/lib/meilisearch",
                "sudo chmod 755 /var/lib/meilisearch",
            ]
            
            for cmd in commands:
                print(f"\n  Running: {cmd}")
                status, output, error = self.execute_command(cmd)
                if status != 0 and "already exists" not in error:
                    print(f"⚠️  Command failed: {error}")
            
            # Read master key from local .env file
            print("\n🔑 Reading master key from local .env file...")
            local_env = CONFIG['paths']['local_env']
            master_key = None
            
            try:
                with open(local_env, 'r') as f:
                    for line in f:
                        if line.startswith('MEILISEARCH_MASTER_KEY='):
                            master_key = line.split('=', 1)[1].strip()
                            break
                
                if not master_key:
                    raise ValueError("MEILISEARCH_MASTER_KEY not found in .env file")
                    
                print("  ✅ Master key found in .env file")
                
            except Exception as e:
                print(f"  ❌ Could not read master key from .env: {e}")
                return False

            # Create systemd service file
            print("\n⚙️  Setting up Meilisearch as a service...")
            service_content = f"""[Unit]
Description=Meilisearch
After=network.target

[Service]
Type=exec
User=bitnami
Group=bitnami
WorkingDirectory=/home/bitnami
Environment=MEILI_MASTER_KEY={master_key}
Environment=HOME=/home/bitnami
ExecStart=/usr/local/bin/meilisearch --env production --db-path /home/bitnami/meilisearch-data --http-addr 127.0.0.1:7700
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target"""
            
            # Completely remove old service file and recreate
            self.execute_command("sudo rm -f /etc/systemd/system/meilisearch.service", show_output=False)
            self.execute_command("sudo systemctl daemon-reload", show_output=False)
            
            # Write new service file
            print("  📝 Creating new service file...")
            self.execute_command(
                f"echo '{service_content}' | sudo tee /etc/systemd/system/meilisearch.service"
            )
            
            # Enable and start service
            print("\n🚀 Starting Meilisearch service...")
            # Stop and disable any existing service first
            self.execute_command("sudo systemctl stop meilisearch", show_output=False)
            self.execute_command("sudo systemctl disable meilisearch", show_output=False)
            
            # Verify the service file was written correctly
            print("  🔍 Verifying service file...")
            status, output, _ = self.execute_command("grep 'meilisearch-data' /etc/systemd/system/meilisearch.service")
            if status != 0:
                print("  ❌ Service file not updated correctly!")
                return False
            print("  ✅ Service file contains correct data path")
            
            # Aggressive reload
            self.execute_command("sudo systemctl daemon-reload")
            self.execute_command("sudo systemctl reset-failed meilisearch", show_output=False)
            self.execute_command("sudo systemctl enable meilisearch")
            
            # Create data directory
            print("  📁 Creating Meilisearch data directory...")
            self.execute_command("mkdir -p /home/bitnami/meilisearch-data", show_output=False)
            self.execute_command("chown bitnami:bitnami /home/bitnami/meilisearch-data", show_output=False)
            self.execute_command("chmod 755 /home/bitnami/meilisearch-data", show_output=False)
            
            # Test running Meilisearch directly first (not as service)
            print("  🧪 Testing Meilisearch directly...")
            status, output, error = self.execute_command(
                f"timeout 3 bash -c 'MEILI_MASTER_KEY={master_key} /usr/local/bin/meilisearch --env production --db-path /home/bitnami/meilisearch-data --http-addr 127.0.0.1:7700'"
            )
            if "listening on: 127.0.0.1:7700" in error or "starting service" in error:
                print("  ✅ Meilisearch can run directly!")
            else:
                print(f"  ❌ Meilisearch failed to run directly. Output: {output}")
                print(f"  Error: {error}")
                return False
            
            # Now try systemd service
            self.execute_command("sudo systemctl start meilisearch")
            
            # Check service status
            print("\n✅ Checking Meilisearch status...")
            status, output, _ = self.execute_command("sudo systemctl status meilisearch --no-pager")
            print(output)
            
            # Test Meilisearch connection
            print("\n🔍 Testing Meilisearch connection...")
            status, output, _ = self.execute_command("curl -s http://localhost:7700/health")
            if "available" in output.lower():
                print("✅ Meilisearch is running and healthy!")
            else:
                print("⚠️  Meilisearch may not be running properly")
            
            # Display configuration info
            print(f"\n📋 Your .env file should have:")
            print(f"MEILISEARCH_URL=http://localhost:7700")
            print(f"MEILISEARCH_MASTER_KEY={master_key}")
            print(f"MEILISEARCH_INDEX_NAME=posts")
            
            print("\n✅ Meilisearch installation completed!")
            
            # Post-flight check: Verify installation is working
            print("\n🔍 Post-flight check: Verifying Meilisearch installation...")
            
            # Check if service is active
            print("  📋 Checking service status...")
            status, output, _ = self.execute_command("sudo systemctl is-active meilisearch", show_output=False)
            if status == 0:
                print("  ✅ Meilisearch service is running")
            else:
                print("  ❌ Meilisearch service is not running")
                print("  📋 Service logs (last 15 lines):")
                status, output, _ = self.execute_command("sudo journalctl -u meilisearch --no-pager -n 15", show_output=False)
                if output:
                    for line in output.strip().split('\n'):
                        print(f"    {line}")
                
                print("  ⚠️  Common fixes:")
                print("    - Check if port 7700 is already in use")
                print("    - Verify /var/lib/meilisearch permissions")
                print("    - Check master key configuration")
                return False
            
            # Check if responding to HTTP requests
            print("  🌐 Testing HTTP connection...")
            import time
            time.sleep(2)  # Give service a moment to be ready
            status, output, _ = self.execute_command("curl -s http://localhost:7700/health", show_output=False)
            if status == 0:
                print("  ✅ Meilisearch is responding on port 7700")
            else:
                print("  ❌ Meilisearch is not responding on port 7700")
                print("  ⚠️  Check firewall settings and service logs")
                return False
            
            # Check version
            print("  🔢 Checking version...")
            status, output, _ = self.execute_command("curl -s http://localhost:7700/version", show_output=False)
            if status == 0 and output:
                print(f"  ✅ Meilisearch version info: {output.strip()}")
            
            print("\n🎉 Meilisearch is fully operational!")
            print("\n⚠️  Remember to:")
            print("  1. Add the Meilisearch settings to your server's .env file")
            print("  2. Restart your Django application")
            print("  3. Run index initialization in Django shell or script")
            
            return True
            
        except Exception as e:
            print(f"\n❌ Meilisearch installation failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
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
            
            # Copy .env file from local to server
            print("\n📄 Copying .env file to server...")
            local_env = CONFIG['paths'].get('local_env', './.env')
            if not Path(local_env).exists():
                raise RuntimeError(f"Local .env file not found at {local_env}. Cannot deploy without environment configuration!")
            
            try:
                with SCPClient(self.ssh_client.get_transport()) as scp:
                    # Copy to temp location first (bitnami home directory)
                    temp_env_path = "/tmp/.env"
                    scp.put(local_env, temp_env_path)
                    print(f"✅ .env file uploaded to temporary location")
                    
                    # Move to project directory with sudo
                    remote_env_path = f"{project_path}/.env"
                    self.execute_command(f"sudo mv {temp_env_path} {remote_env_path}")
                    
                    # Set proper permissions
                    self.execute_command(f"sudo chown {CONFIG['server']['user']}:{CONFIG['server']['user']} {remote_env_path}")
                    self.execute_command(f"sudo chmod 600 {remote_env_path}")  # Secure permissions for .env
                    print(f"✅ .env file moved to {remote_env_path} with secure permissions")
            except Exception as e:
                raise RuntimeError(f"Failed to copy .env file to server: {e}")
            
            # Fix virtual environment permissions
            print("\n🔧 Fixing virtual environment permissions...")
            venv_path = CONFIG['paths']['remote_venv']
            status, _, _ = self.execute_command(f"sudo chown -R bitnami:bitnami {venv_path}")
            if status != 0:
                print("⚠️  Warning: Could not fix venv permissions")
            
            # Activate virtual environment and install dependencies
            print("\n📦 Installing/updating dependencies...")
            status, output, error = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && pip install -r requirements.txt"
            )
            if status != 0:
                raise RuntimeError(f"Failed to install dependencies. Django will not work without them.\n{error}")
            
            # Fix database permissions before migrations
            print("\n🔧 Fixing database permissions...")
            status, _, _ = self.execute_command(f"sudo chown bitnami:bitnami {project_path}/db.sqlite3")
            status, _, _ = self.execute_command(f"sudo chmod 664 {project_path}/db.sqlite3")
            # SQLite also needs write access to the directory
            status, _, _ = self.execute_command(f"sudo chown bitnami:bitnami {project_path}")
            status, _, _ = self.execute_command(f"sudo chmod 775 {project_path}")
            
            # Run migrations
            print("\n🗄️  Running database migrations...")
            status, output, _ = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && python manage.py migrate"
            )
            if status != 0:
                print("⚠️  Warning: Migrations may have failed")
            
            # Fix static files permissions before collecting
            print("\n🔧 Fixing static files permissions...")
            status, _, _ = self.execute_command(f"sudo chown -R bitnami:bitnami {project_path}/static")
            status, _, _ = self.execute_command(f"sudo chmod -R 775 {project_path}/static")
            
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
        
        # Check Meilisearch first - don't waste time uploading if it will fail later
        print("\n🔍 Pre-flight check: Verifying Meilisearch...")
        status, output, _ = self.execute_command("sudo systemctl is-active meilisearch", show_output=False)
        
        if status != 0:
            print("  ⚠️  Meilisearch service not found or not running")
            print("  🔍 Checking if Meilisearch is installed...")
            status, _, _ = self.execute_command("which meilisearch", show_output=False)
            
            if status != 0:
                print("  ❌ Meilisearch not installed. Please run option 4 to install Meilisearch first.")
                print("  🚫 Database deployment cancelled to avoid wasted upload time.")
                return False
            else:
                print("  🔄 Attempting to start Meilisearch service...")
                status, _, error = self.execute_command("sudo systemctl start meilisearch")
                if status != 0:
                    print(f"  ❌ Failed to start Meilisearch: {error}")
                    print("  🚫 Database deployment cancelled. Please fix Meilisearch first.")
                    return False
                
                # Wait and verify it's actually stable (not crashing immediately)
                print("  ⏳ Waiting to verify service stability...")
                import time
                time.sleep(3)
                
                status, _, _ = self.execute_command("sudo systemctl is-active meilisearch", show_output=False)
                if status != 0:
                    print("  💥 Meilisearch service crashed immediately after starting!")
                    print("  📋 Service logs (last 10 lines):")
                    status, output, _ = self.execute_command("sudo journalctl -u meilisearch --no-pager -n 10", show_output=False)
                    if output:
                        for line in output.strip().split('\n'):
                            print(f"    {line}")
                    print("  🚫 Database deployment cancelled. Please fix Meilisearch first.")
                    return False
                    
                print("  ✅ Meilisearch service started and is stable")
        else:
            print("  ✅ Meilisearch is running")
        
        try:
            project_path = CONFIG['paths']['remote_project']
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            
            # Create backup of current database
            print("\n💾 Backing up current database...")
            backup_file = f"db.sqlite3.backup.{timestamp}"
            status, _, _ = self.execute_command(
                f"cd {project_path} && [ -f db.sqlite3 ] && sudo cp db.sqlite3 {backup_file}"
            )
            if status == 0:
                print(f"✅ Backup created: {backup_file}")
            
            # Upload new database (copy to temp first, then move)
            print(f"\n📤 Uploading database from {local_db}...")
            temp_db_path = f"/tmp/db_upload_{timestamp}.sqlite3"
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(local_db, temp_db_path)
            
            # Move from temp to final location with proper permissions
            remote_db_path = f"{project_path}/db.sqlite3"
            status, _, error = self.execute_command(f"sudo mv {temp_db_path} {remote_db_path}")
            if status != 0:
                raise RuntimeError(f"Failed to move database to final location: {error}")
            
            # Fix database permissions
            self.execute_command(f"sudo chown bitnami:bitnami {remote_db_path}")
            self.execute_command(f"sudo chmod 664 {remote_db_path}")
            print("✅ Database uploaded successfully!")
            
            # Set permissions
            print("\n🔒 Setting database permissions...")
            self.execute_command(f"sudo chown www-data:www-data {project_path}/db.sqlite3")
            self.execute_command(f"sudo chmod 664 {project_path}/db.sqlite3")
            
            # Restart services
            self.restart_services()
            
            # Always rebuild search index when database is updated
            print("\n🔍 Rebuilding Meilisearch index (required after database update)...")
            venv_path = CONFIG['paths']['remote_venv']
            
            # Check if Meilisearch is installed and running
            print("  🔍 Checking Meilisearch status...")
            status, output, _ = self.execute_command("sudo systemctl is-active meilisearch", show_output=False)
            
            if status != 0:
                print("  ⚠️  Meilisearch service not found or not running")
                print("  🔍 Checking if Meilisearch is installed...")
                status, _, _ = self.execute_command("which meilisearch", show_output=False)
                
                if status != 0:
                    print("  ❌ Meilisearch not installed. Please run option 4 to install Meilisearch first.")
                    return True  # Don't fail the database deployment for this
                else:
                    print("  🔄 Starting Meilisearch service...")
                    status, _, error = self.execute_command("sudo systemctl start meilisearch")
                    if status != 0:
                        print(f"  ❌ Failed to start Meilisearch: {error}")
                        return True  # Don't fail the database deployment for this
            
            # First clear the Meilisearch data completely
            print("  🧹 Clearing Meilisearch data...")
            self.execute_command(
                "sudo systemctl stop meilisearch",
                show_output=False
            )
            self.execute_command(
                "sudo rm -rf /var/lib/meilisearch/*",
                show_output=False
            )
            self.execute_command(
                "sudo systemctl start meilisearch",
                show_output=False
            )
            
            # Wait for Meilisearch to be ready
            print("  ⏳ Waiting for Meilisearch to restart...")
            import time
            time.sleep(5)
            
            # Verify Meilisearch is responding
            print("  🔍 Verifying Meilisearch connection...")
            status, _, _ = self.execute_command("curl -s http://localhost:7700/health", show_output=False)
            if status != 0:
                print("  ❌ Meilisearch is not responding. Checking service status...")
                
                # Check if service crashed
                status, output, _ = self.execute_command("sudo systemctl is-active meilisearch", show_output=False)
                if status != 0:
                    print("  💥 Meilisearch service has crashed!")
                    
                    # Show service logs for debugging
                    print("  📋 Service logs (last 10 lines):")
                    status, output, _ = self.execute_command("sudo journalctl -u meilisearch --no-pager -n 10", show_output=False)
                    if output:
                        for line in output.strip().split('\n'):
                            print(f"    {line}")
                    
                    print("  ⚠️  Common fixes:")
                    print("    - Check if port 7700 is already in use")
                    print("    - Verify /var/lib/meilisearch permissions")
                    print("    - Check systemd service configuration")
                
                print("  ⚠️  Search functionality may not work.")
                return True  # Don't fail the database deployment for this
            
            # Now rebuild the index
            print("  📝 Re-indexing all posts...")
            status, output, error = self.execute_command(
                f"cd {project_path} && source {venv_path}/bin/activate && "
                f"python manage.py shell -c \"from search.search import clear_search_index, initialize_search_index, bulk_index_posts; "
                f"from posts.models import Post; "
                f"clear_search_index(); initialize_search_index(); "
                f"bulk_index_posts(Post.objects.filter(status='published'))\"",
                show_output=True
            )
            
            if status == 0:
                print("✅ Search index rebuilt!")
            else:
                print(f"  ❌ Failed to rebuild search index: {error}")
                print("  ⚠️  Database deployment successful, but search indexing failed.")
            
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
    print("4. Install/Configure Meilisearch")
    print("5. Exit")
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
        choice = input("Enter choice [1-5]: ").strip()
        
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
            print("  • Copy local .env file to server")
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
            print("  • Clear and rebuild Meilisearch index (required)")
            
            if input("\n⚠️  This will replace the server database! Proceed? (y/n): ").lower() == 'y':
                deployer.deploy_database_update()
        
        elif choice == '4':
            print("\n" + "=" * 50)
            print("MEILISEARCH INSTALLATION")
            print("=" * 50)
            
            print("\nThis will:")
            print("  • Download and install Meilisearch binary")
            print("  • Set up Meilisearch as a systemd service")
            print("  • Configure it to run on localhost:7700")
            print("  • Generate configuration for your .env file")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.install_meilisearch()
        
        elif choice == '5':
            print("\n👋 Exiting deployment script")
            break
        
        else:
            print("\n❌ Invalid choice. Please enter 1-5.")
    
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