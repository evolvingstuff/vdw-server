#!/usr/bin/env python3
"""
Docker-based deployment script for VDW Server
Clean, simple deployment without the Bitnami nightmare
"""

import os
import sys
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv
import paramiko
from scp import SCPClient

# Load environment variables
load_dotenv()

class DockerDeployment:
    def __init__(self):
        self.config = {
            'instance_id': os.getenv('EC2_INSTANCE_ID'),
            'host': os.getenv('DEPLOY_HOST'),
            'user': os.getenv('DEPLOY_USER'),
            'port': int(os.getenv('DEPLOY_PORT')),
            'key_file': os.getenv('DEPLOY_KEY_FILE'),
            'app_path': os.getenv('DEPLOY_APP_PATH'),
            'local_db': os.getenv('DEPLOY_LOCAL_DB'),
            'django_port': os.getenv('DJANGO_PORT'),
        }
        
        # Validate required config (instance_id only required for provisioning)
        required_fields = ['host', 'user', 'port', 'key_file', 'app_path', 'local_db', 'django_port']
        for field in required_fields:
            if not self.config[field]:
                print(f"❌ {field.upper().replace('_', '_')} not set in .env file")
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
            print(f"🌐 Site should be available at: http://{self.config['host']}:{self.config['django_port']}")
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
    
    def run_aws_command(self, command, silent_on_error=False):
        """Run AWS CLI command"""
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                if not silent_on_error:
                    print(f"❌ AWS command failed: {result.stderr}")
                return False, result.stderr
            return True, result.stdout.strip()
        except Exception as e:
            if not silent_on_error:
                print(f"❌ AWS command error: {e}")
            return False, str(e)
    
    def wait_for_instance(self):
        """Wait for EC2 instance to be running"""
        print(f"⏳ Waiting for instance {self.config['instance_id']} to be running...")
        
        max_attempts = 30
        for attempt in range(max_attempts):
            success, output = self.run_aws_command(
                f"aws ec2 describe-instances --instance-ids {self.config['instance_id']} "
                "--query 'Reservations[0].Instances[0].State.Name' --output text"
            )
            
            if success and output == "running":
                print("✅ Instance is running!")
                return True
            
            if success:
                print(f"   Instance state: {output}")
            
            time.sleep(10)
        
        print(f"❌ Instance didn't start within {max_attempts * 10} seconds")
        return False
    
    def configure_security_group(self):
        """Add ports to security group"""
        print("🔒 Configuring security group...")
        
        # Get security group ID
        success, sg_id = self.run_aws_command(
            f"aws ec2 describe-instances --instance-ids {self.config['instance_id']} "
            "--query 'Reservations[0].Instances[0].SecurityGroups[0].GroupId' --output text"
        )
        
        if not success:
            print(f"❌ Failed to get security group ID: {sg_id}")
            return False
        
        print(f"   Security Group ID: {sg_id}")
        
        # Add Django port
        django_port = self.config['django_port']
        print(f"   Adding port {django_port} (Django)...")
        success, output = self.run_aws_command(
            f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
            f"--protocol tcp --port {django_port} --cidr 0.0.0.0/0",
            silent_on_error=True
        )
        
        if not success:
            if "already exists" in output:
                print(f"   Port {django_port} already configured ✓")
            else:
                print(f"❌ Failed to add port {django_port}: {output}")
                return False
        else:
            print(f"   Port {django_port} added ✓")
        
        # Add port 7700 (Meilisearch)
        print("   Adding port 7700 (Meilisearch)...")
        success, output = self.run_aws_command(
            f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
            "--protocol tcp --port 7700 --cidr 0.0.0.0/0",
            silent_on_error=True
        )
        
        if not success:
            if "already exists" in output:
                print("   Port 7700 already configured ✓")
            else:
                print(f"❌ Failed to add port 7700: {output}")
                return False
        else:
            print("   Port 7700 added ✓")
        
        print("✅ Security group configured!")
        return True
    
    def install_docker(self):
        """Install Docker and Docker Compose on the server"""
        print("🐳 Installing Docker...")
        
        # Check if Docker is already installed
        success, output, error = self.execute_command("docker --version", show_output=False)
        if success:
            print("✅ Docker already installed, skipping installation")
            return True
        
        commands = [
            "sudo apt-get update",
            "sudo apt-get install -y ca-certificates curl gnupg lsb-release",
            "sudo mkdir -p /etc/apt/keyrings",
            "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --batch --yes -o /etc/apt/keyrings/docker.gpg",
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null',
            "sudo apt-get update",
            "sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            "sudo usermod -aG docker $USER",
        ]
        
        for cmd in commands:
            print(f"   Running: {cmd}")
            success, output, error = self.execute_command(cmd, show_output=False)
            if not success:
                print(f"❌ Command failed: {error}")
                return False
        
        print("✅ Docker installed successfully!")
        return True
    
    def setup_environment(self):
        """Set up environment variables"""
        print("🔧 Setting up environment...")
        
        # Check if local .env exists
        local_env = Path('.env')
        if not local_env.exists():
            print("❌ Local .env file not found. Please create one with your configuration.")
            return False
        
        # Upload .env file
        print("   Uploading .env file...")
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(str(local_env), f"{self.config['app_path']}/.env")
            print("✅ Environment file uploaded!")
            return True
        except Exception as e:
            print(f"❌ Failed to upload .env: {e}")
            return False
    
    def provision_server(self):
        """Complete server provisioning"""
        print("\n🚀 Starting server provisioning...\n")
        
        # Check for EC2 instance ID
        if not self.config['instance_id']:
            raise ValueError("EC2_INSTANCE_ID not set in .env file (required for provisioning)")
        
        # Step 1: Wait for instance to be running
        if not self.wait_for_instance():
            return False
        
        # Step 2: Configure security group
        if not self.configure_security_group():
            return False
        
        # Step 3: Connect via SSH
        if not self.connect():
            return False
        
        try:
            # Step 4: Install Docker
            if not self.install_docker():
                return False
            
            # Step 5: Upload application code
            if not self.upload_code():
                return False
            
            # Step 6: Set up environment
            if not self.setup_environment():
                return False
            
            print("\n🎉 Server provisioning completed successfully!")
            print(f"🌐 Server ready at: {self.config['host']}:{self.config['django_port']}")
            print("📋 Next steps:")
            print("   1. Wait ~30 seconds for Docker group changes to take effect")
            print("   2. Use option 3 (Full Deploy) to deploy your application")
            
            return True
            
        except Exception as e:
            print(f"❌ Provisioning failed: {e}")
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
    print("0. Provision Server (initial setup: install Docker, upload code, configure)")
    print("1. Deploy Code (upload code + rebuild containers)")
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
        choice = input("Enter choice [0-6]: ").strip()
        
        if choice == '0':
            print("\n" + "=" * 50)
            print("SERVER PROVISIONING")
            print("=" * 50)
            print("This will:")
            print("  • Wait for EC2 instance to be running")
            print("  • Configure security group ports")
            print("  • Install Docker and Docker Compose")
            print("  • Upload application code")
            print("  • Set up environment variables")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.provision_server()
        
        elif choice == '1':
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