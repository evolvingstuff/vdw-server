#!/usr/bin/env python3
"""
Server provisioning script for VDW Server
Handles initial EC2 setup, Docker installation, and environment configuration
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

class ServerProvisioner:
    def __init__(self):
        self.config = {
            'instance_id': os.getenv('EC2_INSTANCE_ID'),
            'host': os.getenv('DEPLOY_HOST'),
            'user': os.getenv('DEPLOY_USER', 'ubuntu'),
            'port': int(os.getenv('DEPLOY_PORT', 22)),
            'key_file': os.getenv('DEPLOY_KEY_FILE'),
            'app_path': os.getenv('DEPLOY_APP_PATH', '/app'),
        }
        
        # Validate required config
        required_fields = ['instance_id', 'host', 'key_file']
        for field in required_fields:
            if not self.config[field]:
                print(f"❌ {field.upper()} not set in .env file")
                sys.exit(1)
        
        self.ssh_client = None
    
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
        """Add ports 8000 and 7700 to security group"""
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
        
        # Add port 8000 (Django)
        print("   Adding port 8000 (Django)...")
        success, output = self.run_aws_command(
            f"aws ec2 authorize-security-group-ingress --group-id {sg_id} "
            "--protocol tcp --port 8000 --cidr 0.0.0.0/0",
            silent_on_error=True
        )
        
        if not success:
            if "already exists" in output:
                print("   Port 8000 already configured ✓")
            else:
                print(f"❌ Failed to add port 8000: {output}")
                return False
        else:
            print("   Port 8000 added ✓")
        
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
    
    def connect_ssh(self):
        """Establish SSH connection"""
        try:
            print(f"🔌 Connecting to {self.config['host']}...")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.ssh_client.connect(
                hostname=self.config['host'],
                username=self.config['user'],
                port=self.config['port'],
                key_filename=os.path.expanduser(self.config['key_file'])
            )
            
            print("✅ SSH connected!")
            return True
            
        except Exception as e:
            print(f"❌ SSH connection failed: {e}")
            return False
    
    def disconnect_ssh(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print("🔌 SSH disconnected")
    
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
            if error and exit_status != 0:
                print(f"⚠️  {error}")
            
            return exit_status == 0, output, error
            
        except Exception as e:
            print(f"❌ Command execution failed: {e}")
            return False, "", str(e)
    
    def install_docker(self):
        """Install Docker and Docker Compose on the server"""
        print("🐳 Installing Docker...")
        
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
    
    def upload_code(self):
        """Upload application code via SCP"""
        print("📤 Uploading application code...")
        
        app_path = self.config['app_path']
        
        # Create app directory with proper permissions
        success, output, error = self.execute_command(f"sudo mkdir -p {app_path}")
        if not success:
            print(f"❌ Failed to create app directory: {error}")
            return False
        
        # Set ownership to the user
        success, output, error = self.execute_command(f"sudo chown {self.config['user']}:{self.config['user']} {app_path}")
        if not success:
            print(f"❌ Failed to set directory ownership: {error}")
            return False
        
        # Files/directories to upload (excluding what's in .dockerignore)
        exclude_patterns = [
            '.git', '__pycache__', '*.pyc', '*.pyo', '*.pyd', 
            '.venv', 'venv/', '.env', '.DS_Store', '*.log',
            '.pytest_cache', '.coverage', '.vscode', '.idea',
            '*.swp', '*.swo', 'db.sqlite3', '*.sqlite3'
        ]
        
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
        
        # Step 1: Wait for instance to be running
        if not self.wait_for_instance():
            return False
        
        # Step 2: Configure security group
        if not self.configure_security_group():
            return False
        
        # Step 3: Connect via SSH
        if not self.connect_ssh():
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
            print(f"🌐 Server ready at: {self.config['host']}")
            print("📋 Next steps:")
            print("   1. Wait ~30 seconds for Docker group changes to take effect")
            print("   2. Run 'python deploy-docker.py' to deploy your application")
            
            return True
            
        except Exception as e:
            print(f"❌ Provisioning failed: {e}")
            return False
        
        finally:
            self.disconnect_ssh()

def print_header():
    """Print script header"""
    print("\n" + "=" * 50)
    print("    VDW Server Provisioning")
    print("=" * 50)

def main():
    print_header()
    
    # Check for required environment variables
    required_vars = ['EC2_INSTANCE_ID', 'DEPLOY_HOST', 'DEPLOY_KEY_FILE']
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    
    if missing_vars:
        print("\n❌ Missing required environment variables!")
        print(f"Please set the following in your .env file: {', '.join(missing_vars)}")
        print("\nExample .env configuration:")
        print("EC2_INSTANCE_ID=i-1234567890abcdef0")
        print("DEPLOY_HOST=ec2-xxx-xxx-xxx-xxx.compute-1.amazonaws.com")
        print("DEPLOY_USER=ubuntu")
        print("DEPLOY_KEY_FILE=~/.ssh/your-key.pem")
        print("DEPLOY_APP_PATH=/app")
        sys.exit(1)
    
    provisioner = ServerProvisioner()
    success = provisioner.provision_server()
    
    if success:
        print("\n✨ Provisioning complete! Your server is ready for deployment.")
    else:
        print("\n💥 Provisioning failed. Please check the errors above.")
        sys.exit(1)

if __name__ == "__main__":
    main()