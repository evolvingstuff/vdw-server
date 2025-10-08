from django.apps import AppConfig


class PagesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pages'
    label = 'posts'
    verbose_name = 'Pages'

    def ready(self):
        import pages.signals
