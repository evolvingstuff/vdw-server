#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
from helper_functions.meilisearch import *


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')

    # Default to runserver if no arguments provided
    if len(sys.argv) == 1:
        sys.argv.append('runserver')

    # Check and start Meilisearch only when running the development server
    if 'runserver' in sys.argv:
        print("Checking for Meilisearch instance...")
        start_meilisearch()

        print("\nStart admin at: http://127.0.0.1:8000/admin/\n")

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()