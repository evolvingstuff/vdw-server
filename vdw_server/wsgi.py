"""
WSGI config for vdw_server project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()

from vdw_server.startup import run_startup_tasks

run_startup_tasks()
