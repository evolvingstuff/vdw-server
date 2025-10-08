from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from .models import Page
from search.search import index_page, remove_page_from_search


@receiver(post_save, sender=Page)
def sync_page_to_search_on_save(sender, instance, created, **kwargs):
    """Automatically sync page to MeiliSearch when saved"""
    if instance.status == 'published':
        # Index published pages
        index_page(instance)
    else:
        # Remove draft pages from search (in case they were published before)
        remove_page_from_search(instance.pk)


@receiver(post_delete, sender=Page)
def remove_page_from_search_on_delete(sender, instance, **kwargs):
    """Automatically remove page from MeiliSearch when deleted"""
    remove_page_from_search(instance.pk)


@receiver(m2m_changed, sender=Page.tags.through)
def sync_page_to_search_on_tags_change(sender, instance, action, **kwargs):
    """Re-index page when tags are added or removed"""
    # Only re-index after tags have been added/removed/cleared
    if action in ['post_add', 'post_remove', 'post_clear']:
        if instance.status == 'published':
            index_page(instance)
