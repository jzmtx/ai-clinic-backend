# clinic_token_system/__init__.py

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
from .celery import app as celery_app

# Set the default Django settings module for the 'celery' program.
# This is crucial for the shell and other management commands.
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'clinic_token_system.settings')

# Load the configuration from your Django settings.
celery_app.config_from_object('django.conf:settings', namespace='CELERY')

__all__ = ('celery_app',)