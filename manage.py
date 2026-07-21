#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from collections.abc import Mapping

import django
from django.core.management import call_command, execute_from_command_line

from helper_functions.meilisearch import start_meilisearch


def should_reindex_on_runserver(argv, environ) -> bool:
    assert isinstance(argv, list), f"argv must be list, got {type(argv)}"
    assert isinstance(environ, Mapping), f"environ must be Mapping, got {type(environ)}"

    if 'runserver' not in argv:
        return False
    if '--noreload' in argv:
        return True
    if 'RUN_MAIN' in environ and environ['RUN_MAIN'] == 'true':
        return True
    return False


def prepare_runserver() -> None:
    """Apply schema changes before rebuilding search for the dev server."""

    django.setup()
    print("Applying database migrations before runserver...")
    call_command('migrate', interactive=False)
    print("Reindexing Meilisearch before runserver...")
    call_command('reindex_search')


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')

    # Default to runserver if no arguments provided
    if len(sys.argv) == 1:
        sys.argv.append('runserver')

    # Check and start Meilisearch only when running the development server locally (not in Docker)
    if 'runserver' in sys.argv and not os.getenv('RUNNING_IN_DOCKER'):
        print("Checking for Meilisearch instance...")
        start_meilisearch()

        print("\nStart admin at: http://127.0.0.1:8000/admin/\n")
    if should_reindex_on_runserver(sys.argv, os.environ):
        prepare_runserver()

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
