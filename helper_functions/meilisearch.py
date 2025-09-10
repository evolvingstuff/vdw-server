import socket
import subprocess
import sys
import time


def check_meilisearch(host='127.0.0.1', port=7700):
    """Check if Meilisearch is running by checking its port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) == 0


def start_meilisearch():
    """Start the Meilisearch process in the background."""
    print("Attempting to start Meilisearch in the background...")
    try:
        # Use Popen to start the process without blocking the script.
        # stdout and stderr are redirected to DEVNULL to keep the console clean.
        subprocess.Popen(['meilisearch'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)  # Give it a moment to initialize
        if check_meilisearch():
            print("Meilisearch started successfully. ✅")
        else:
            print("Meilisearch failed to start. Please check its installation.")
            sys.exit(1)
    except FileNotFoundError:
        print("\nError: The 'meilisearch' command was not found.")
        print("Please ensure Meilisearch is installed and that its location is in your system's PATH.\n")
        sys.exit(1)