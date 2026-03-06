from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from site_pages.models import SitePage
from vdw_server.not_found_suggestions import (
    remove_site_page_not_found_suggestion,
    upsert_site_page_not_found_suggestion,
)


@receiver(post_save, sender=SitePage)
def sync_site_page_to_not_found_cache_on_save(sender, instance, **kwargs):
    """Keep the in-memory 404 suggestion cache current for site pages."""

    upsert_site_page_not_found_suggestion(instance)


@receiver(post_delete, sender=SitePage)
def remove_site_page_from_not_found_cache_on_delete(sender, instance, **kwargs):
    """Remove deleted site pages from the in-memory 404 suggestion cache."""

    if not instance.pk:
        return
    remove_site_page_not_found_suggestion(instance.pk)
