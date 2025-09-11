import socket
import subprocess
import sys
import time


def start_meilisearch():
    """Kill any existing Meilisearch process and start a fresh one."""
    # Check if meilisearch is already running
    result = subprocess.run(['pgrep', '-f', 'meilisearch'], capture_output=True, text=True)
    if result.returncode == 0:
        print("Killing existing Meilisearch processes...")
        subprocess.run(['pkill', '-f', 'meilisearch'], check=True)
        time.sleep(1)  # Give it a moment to die
    
    print("Starting fresh Meilisearch process in the background...")
    # Use Popen to start the process without blocking the script.
    # stdout and stderr are redirected to DEVNULL to keep the console clean.
    subprocess.Popen(['meilisearch'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # Give it a moment to initialize
    print("Meilisearch started successfully. ✅")