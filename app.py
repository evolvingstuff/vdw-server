#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')
    from django.core.management import execute_from_command_line
    
    # Default to runserver if no arguments provided
    if len(sys.argv) == 1:
        sys.argv.append('runserver')
    
    # Show admin URL message when running server
    if 'runserver' in sys.argv:
        print("\nStart admin at: http://127.0.0.1:8000/admin/\n")
    
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
