#!/usr/bin/env python3
"""
Development helper script for hybrid venv/Docker workflows
"""
import subprocess
import sys
import os
from pathlib import Path

def run_command(cmd, description):
    """Run a command and show output"""
    print(f"\n🔧 {description}...")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"❌ Failed: {description}")
        sys.exit(1)
    print(f"✅ {description} completed")

def main():
    if len(sys.argv) < 2:
        print("""
VDW Server Development Helper

Usage: python dev.py <command>

Available commands:

🐍 VENV DEVELOPMENT (PyCharm debugging):
  venv-meilisearch    Start only Meilisearch in Docker (for venv Django)
  venv-stop           Stop Meilisearch container

🐳 DOCKER DEVELOPMENT (production parity):
  docker-build        Build and start full stack
  docker-start        Start existing containers  
  docker-stop         Stop all containers
  docker-logs         Show container logs
  docker-shell        Open shell in Django container

🔧 UTILITIES:
  reindex             Reindex search (works in both environments)
  migrate             Run database migrations
  
Examples:
  python dev.py venv-meilisearch   # Start Meilisearch, run Django in venv
  python dev.py docker-build      # Full Docker stack for testing
        """)
        sys.exit(1)
    
    command = sys.argv[1]
    
    # VENV DEVELOPMENT COMMANDS
    if command == "venv-meilisearch":
        print("🐍 Starting Meilisearch for venv development...")
        run_command("docker compose up meilisearch -d", "Starting Meilisearch container")
        print("\n✨ Meilisearch running at http://localhost:7700")
        print("💡 Now run Django in your venv: python manage.py runserver")
        
    elif command == "venv-stop":
        run_command("docker compose stop meilisearch", "Stopping Meilisearch container")
    
    # DOCKER DEVELOPMENT COMMANDS    
    elif command == "docker-build":
        print("🐳 Building and starting full Docker stack...")
        run_command("docker compose up --build -d", "Building and starting containers")
        print("\n✨ Full stack running:")
        print("   Django: http://localhost:8000")
        print("   Meilisearch: http://localhost:7700")
        
    elif command == "docker-start":
        run_command("docker compose up -d", "Starting existing containers")
        
    elif command == "docker-stop":
        run_command("docker compose stop", "Stopping all containers")
        
    elif command == "docker-logs":
        run_command("docker compose logs -f", "Showing container logs")
        
    elif command == "docker-shell":
        run_command("docker compose exec django /bin/bash", "Opening Django container shell")
    
    # UTILITY COMMANDS
    elif command == "reindex":
        if Path("/.dockerenv").exists():
            # Running inside Docker
            run_command("python manage.py reindex_search", "Reindexing search")
        else:
            # Check if we're in venv or should use Docker
            try:
                import django
                run_command("python manage.py reindex_search", "Reindexing search (venv)")
            except ImportError:
                run_command("docker compose exec django python manage.py reindex_search", "Reindexing search (Docker)")
                
    elif command == "migrate":
        if Path("/.dockerenv").exists():
            run_command("python manage.py migrate", "Running migrations")
        else:
            try:
                import django
                run_command("python manage.py migrate", "Running migrations (venv)")
            except ImportError:
                run_command("docker compose exec django python manage.py migrate", "Running migrations (Docker)")
    
    else:
        print(f"❌ Unknown command: {command}")
        print("Run 'python dev.py' to see available commands")
        sys.exit(1)

if __name__ == "__main__":
    main()