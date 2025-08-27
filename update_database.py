#!/usr/bin/env python
"""Simple database update script - run this after model changes."""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')
django.setup()

from django.core.management import call_command

print("Creating migrations...")
call_command('makemigrations')

print("\nApplying migrations...")
call_command('migrate')

print("\nDatabase updated successfully!")