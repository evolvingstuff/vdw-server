import logging
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from .models import Page
from .recent_cache import upsert_recent_page, remove_recent_page
from search.search import index_page, remove_page_from_search

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Page)
def sync_page_to_search_on_save(sender, instance, created, **kwargs):
    """Automatically sync page to MeiliSearch when saved"""
    if instance.status == 'published':
        # Index published pages
        try:
            index_page(instance)
        except Exception as e:
            # External system failure (MeiliSearch). Log and continue.
            logger.error("MeiliSearch indexing failed on save for Page %s: %s", instance.pk, e)
    else:
        # Remove draft pages from search (in case they were published before)
        try:
            remove_page_from_search(instance.pk)
        except Exception as e:
            logger.error("MeiliSearch removal failed on save for Page %s: %s", instance.pk, e)

    upsert_recent_page(instance)


@receiver(post_delete, sender=Page)
def remove_page_from_search_on_delete(sender, instance, **kwargs):
    """Automatically remove page from MeiliSearch when deleted"""
    try:
        remove_page_from_search(instance.pk)
    except Exception as e:
        logger.error("MeiliSearch removal failed on delete for Page %s: %s", instance.pk, e)

    remove_recent_page(instance.pk)


@receiver(m2m_changed, sender=Page.tags.through)
def sync_page_to_search_on_tags_change(sender, instance, action, **kwargs):
    """Re-index page when tags are added or removed"""
    # Only re-index after tags have been added/removed/cleared
    if action in ['post_add', 'post_remove', 'post_clear']:
        instance.update_derived_tags()
        if instance.status == 'published':
            try:
                index_page(instance)
            except Exception as e:
                logger.error("MeiliSearch indexing failed on tags change for Page %s: %s", instance.pk, e)
