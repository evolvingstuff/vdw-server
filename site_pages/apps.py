from django.apps import AppConfig


class SitePagesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'site_pages'
    label = 'pages'
    verbose_name = 'Site Pages'
