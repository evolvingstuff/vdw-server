"""
S3/CloudFront storage configuration for media files
"""

import os

# AWS S3 Configuration
AWS_ACCESS_KEY_ID = os.environ['AWS_ACCESS_KEY_ID']
AWS_SECRET_ACCESS_KEY = os.environ['AWS_SECRET_ACCESS_KEY']
AWS_STORAGE_BUCKET_NAME = os.environ['AWS_STORAGE_BUCKET_NAME']
AWS_S3_REGION_NAME = os.environ['AWS_DEFAULT_REGION']

# CloudFront Configuration
AWS_S3_CUSTOM_DOMAIN = 'd378j1rmrlek7x.cloudfront.net'

# S3 Storage settings
AWS_S3_OBJECT_PARAMETERS = {
    'CacheControl': 'max-age=86400',  # 1 day cache
}
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = None  # Don't set ACLs - bucket handles permissions
AWS_QUERYSTRING_AUTH = False

# Media files configuration
# Django 4.2+ uses STORAGES setting
STORAGES = {
    'default': {
        'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    }
}
# Keep old setting for Django < 4.2 compatibility
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
MEDIA_URL = f'https://{AWS_S3_CUSTOM_DOMAIN}/'
# No MEDIA_ROOT needed for S3 storage

# Optional: Separate bucket for static files if needed
# STATICFILES_STORAGE = 'storages.backends.s3boto3.S3StaticStorage'
# AWS_STATIC_LOCATION = 'static'