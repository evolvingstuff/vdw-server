#!/usr/bin/env python3
"""
Docker-based deployment script for VDW Server
Clean, simple deployment without the Bitnami nightmare
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv
import paramiko
from scp import SCPClient

# Load environment variables
load_dotenv()

class DockerDeployment:
    def __init__(self):
        self.config = {
            'host': os.getenv('DEPLOY_HOST'),
            'user': os.getenv('DEPLOY_USER', 'ubuntu'),
            'port': int(os.getenv('DEPLOY_PORT', 22)),
            'key_file': os.getenv('DEPLOY_KEY_FILE'),
            'app_path': os.getenv('DEPLOY_APP_PATH', '/app'),
            'local_db': os.getenv('DEPLOY_LOCAL_DB', './db.sqlite3'),
        }
        
        # Validate required config
        if not self.config['host']:
            print("❌ DEPLOY_HOST not set in .env file")
            sys.exit(1)
        
        self.ssh_client = None
    
    def connect(self):
        """Establish SSH connection"""
        try:
            print(f"🔌 Connecting to {self.config['host']}...")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if self.config['key_file']:
                self.ssh_client.connect(
                    hostname=self.config['host'],
                    username=self.config['user'],
                    port=self.config['port'],
                    key_filename=os.path.expanduser(self.config['key_file'])
                )
            else:
                self.ssh_client.connect(
                    hostname=self.config['host'],
                    username=self.config['user'],
                    port=self.config['port']
                )
            
            print("✅ Connected successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print("🔌 Disconnected from server")
    
    def execute_command(self, command, show_output=True):
        """Execute command on remote server"""
        if not self.ssh_client:
            print("❌ Not connected to server")
            return False
        
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()
            
            if show_output and output:
                print(output)
            if error:
                print(f"⚠️  {error}")
            
            return exit_status == 0, output, error
            
        except Exception as e:
            print(f"❌ Command execution failed: {e}")
            return False, "", str(e)
    
    def upload_code(self):
        """Upload application code via SCP"""
        print("📤 Uploading application code...")
        
        app_path = self.config['app_path']
        
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                # Upload all important files
                for pattern in ['*.py', '*.txt', '*.yml', '*.yaml', 'Dockerfile', '.dockerignore']:
                    for file_path in Path('.').glob(pattern):
                        if file_path.name not in ['.env', 'db.sqlite3']:
                            print(f"   Uploading {file_path}...")
                            scp.put(str(file_path), f"{app_path}/{file_path.name}")
                
                # Upload directories (posts, templates, static, etc.)
                for dir_path in Path('.').iterdir():
                    if dir_path.is_dir() and dir_path.name not in ['.git', '__pycache__', '.venv', 'venv', '.pytest_cache', '.idea', '.vscode']:
                        print(f"   Uploading directory {dir_path}...")
                        scp.put(str(dir_path), app_path, recursive=True)
            
            # Set ownership
            success, output, error = self.execute_command(f"sudo chown -R {self.config['user']}:{self.config['user']} {app_path}")
            if not success:
                print(f"❌ Failed to set ownership: {error}")
                return False
            
            print("✅ Code uploaded successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Code upload failed: {e}")
            return False
    
    def deploy_code(self):
        """Deploy code updates via SCP upload + docker rebuild"""
        print("\n🚀 Starting code deployment...")
        
        if not self.connect():
            return False
        
        try:
            app_path = self.config['app_path']
            
            # Upload fresh code from local machine
            print("📦 Uploading fresh code from local machine...")
            if not self.upload_code():
                return False
            
            # Rebuild and restart containers
            print("🐳 Rebuilding Docker containers...")
            success, output, error = self.execute_command(
                f"cd {app_path} && docker compose up --build -d"
            )
            if not success:
                print(f"❌ Docker rebuild failed: {error}")
                return False
            
            # Check container status
            print("🔍 Checking container status...")
            success, output, error = self.execute_command(
                f"cd {app_path} && docker compose ps"
            )
            
            print("✅ Code deployment completed successfully!")
            print(f"🌐 Site should be available at: http://{self.config['host']}")
            return True
            
        except Exception as e:
            print(f"❌ Deployment failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def deploy_database(self):
        """Deploy database update via SCP + container restart"""
        print("\n🗄️  Starting database deployment...")
        
        # Check local database exists
        local_db = Path(self.config['local_db'])
        if not local_db.exists():
            print(f"❌ Local database not found: {local_db}")
            return False
        
        db_size_mb = local_db.stat().st_size / (1024 * 1024)
        print(f"📊 Local database: {local_db} ({db_size_mb:.1f} MB)")
        
        if input(f"\n⚠️  This will replace the server database! Proceed? (y/n): ").lower() != 'y':
            print("❌ Database deployment cancelled")
            return False
        
        if not self.connect():
            return False
        
        try:
            app_path = self.config['app_path']
            
            # Stop Django container to avoid database locks
            print("🛑 Stopping Django container...")
            self.execute_command(f"cd {app_path} && docker compose stop django")
            
            # Remove existing database file/directory
            print("📤 Uploading database...")
            self.execute_command(f"sudo rm -rf {app_path}/db.sqlite3")
            
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(str(local_db), f"{app_path}/db.sqlite3")
            print("✅ Database uploaded successfully!")
            
            # Fix database permissions for Docker container (root access)
            print("🔧 Setting database permissions...")
            success, output, error = self.execute_command(f"sudo chown root:root {app_path}/db.sqlite3")
            if not success:
                print(f"❌ Failed to set database ownership: {error}")
                return False
            
            success, output, error = self.execute_command(f"sudo chmod 644 {app_path}/db.sqlite3")
            if not success:
                print(f"❌ Failed to set database permissions: {error}")
                return False
            
            # Fix directory permissions so SQLite can create temp files  
            success, output, error = self.execute_command(f"sudo chown root:root {app_path}")
            if not success:
                print(f"❌ Failed to set directory ownership: {error}")
                return False
            
            # Start Django container
            print("🚀 Starting Django container...")
            success, output, error = self.execute_command(
                f"cd {app_path} && docker compose start django"
            )
            if not success:
                print(f"❌ Failed to start Django container: {error}")
                return False
            
            # Wait a moment for container to be ready
            print("⏳ Waiting for container to be ready...")
            import time
            time.sleep(3)
            
            # Reindex search
            print("🔍 Rebuilding search index...")
            success, output, error = self.execute_command(
                f"cd {app_path} && docker compose exec -T django python manage.py reindex_search"
            )
            if not success:
                print(f"⚠️  Search reindexing may have failed: {error}")
            else:
                print("✅ Search index rebuilt!")
            
            print("✅ Database deployment completed successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Database deployment failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def deploy_full(self):
        """Deploy both code and database"""
        print("\n🎯 Starting full deployment...")
        
        print("Step 1: Deploying code...")
        if not self.deploy_code():
            print("❌ Code deployment failed, aborting full deployment")
            return False
        
        print("\nStep 2: Deploying database...")
        if not self.deploy_database():
            print("❌ Database deployment failed")
            return False
        
        print("🎉 Full deployment completed successfully!")
        return True
    
    def reindex_search(self):
        """Reindex search without other changes"""
        print("\n🔍 Reindexing search...")
        
        if not self.connect():
            return False
        
        try:
            app_path = self.config['app_path']
            success, output, error = self.execute_command(
                f"cd {app_path} && docker compose exec -T django python manage.py reindex_search"
            )
            
            if success:
                print("✅ Search reindexing completed!")
            else:
                print(f"❌ Search reindexing failed: {error}")
            
            return success
            
        except Exception as e:
            print(f"❌ Search reindexing failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def show_status(self):
        """Show server status and logs"""
        print("\n📊 Checking server status...")
        
        if not self.connect():
            return False
        
        try:
            app_path = self.config['app_path']
            
            print("🐳 Container status:")
            self.execute_command(f"cd {app_path} && docker compose ps")
            
            print("\n📋 Recent logs (last 20 lines):")
            self.execute_command(f"cd {app_path} && docker compose logs --tail=20")
            
            return True
            
        except Exception as e:
            print(f"❌ Status check failed: {e}")
            return False
        
        finally:
            self.disconnect()

def print_header():
    """Print script header"""
    print("\n" + "=" * 50)
    print("    VDW Server Docker Deployment")
    print("=" * 50)

def print_menu():
    """Print deployment menu"""
    print(f"\n🌐 Server: {os.getenv('DEPLOY_HOST', 'not configured')}")
    print(f"📁 App path: {os.getenv('DEPLOY_APP_PATH', '/app')}\n")
    
    print("Select deployment option:\n")
    print("1. Deploy Code (git pull + rebuild containers)")
    print("2. Deploy Database (upload + reindex search)")
    print("3. Full Deploy (code + database)")
    print("4. Reindex Search (rebuild search index only)")
    print("5. Show Status (containers + logs)")
    print("6. Exit")
    print()

def main():
    print_header()
    
    # Check for required environment variables
    if not os.getenv('DEPLOY_HOST'):
        print("\n❌ Missing configuration!")
        print("Please set DEPLOY_HOST in your .env file")
        print("\nExample .env configuration:")
        print("DEPLOY_HOST=your-server.com")
        print("DEPLOY_USER=ubuntu")
        print("DEPLOY_KEY_FILE=~/.ssh/your-key.pem")
        print("DEPLOY_APP_PATH=/app")
        print("DEPLOY_LOCAL_DB=./db.sqlite3")
        sys.exit(1)
    
    deployer = DockerDeployment()
    
    while True:
        print_menu()
        choice = input("Enter choice [1-6]: ").strip()
        
        if choice == '1':
            print("\n" + "=" * 50)
            print("CODE DEPLOYMENT")
            print("=" * 50)
            print("This will:")
            print("  • Pull latest code from GitHub")
            print("  • Rebuild Docker containers")
            print("  • Restart services")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.deploy_code()
        
        elif choice == '2':
            print("\n" + "=" * 50)
            print("DATABASE DEPLOYMENT")
            print("=" * 50)
            deployer.deploy_database()
        
        elif choice == '3':
            print("\n" + "=" * 50)
            print("FULL DEPLOYMENT")
            print("=" * 50)
            print("This will:")
            print("  • Deploy code (git pull + rebuild)")
            print("  • Deploy database (upload + reindex)")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.deploy_full()
        
        elif choice == '4':
            deployer.reindex_search()
        
        elif choice == '5':
            deployer.show_status()
        
        elif choice == '6':
            print("\n👋 Goodbye!")
            break
        
        else:
            print("❌ Invalid choice. Please try again.")

if __name__ == "__main__":
    main()