from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver
from .models import Post
from .search import index_post, remove_post_from_search


@receiver(post_save, sender=Post)
def sync_post_to_search_on_save(sender, instance, created, **kwargs):
    """Automatically sync post to MeiliSearch when saved"""
    if instance.status == 'published':
        # Index published posts
        index_post(instance)
    else:
        # Remove draft posts from search (in case they were published before)
        remove_post_from_search(instance.pk)


@receiver(post_delete, sender=Post)
def remove_post_from_search_on_delete(sender, instance, **kwargs):
    """Automatically remove post from MeiliSearch when deleted"""
    remove_post_from_search(instance.pk)


@receiver(m2m_changed, sender=Post.tags.through)
def sync_post_to_search_on_tags_change(sender, instance, action, **kwargs):
    """Re-index post when tags are added or removed"""
    # Only re-index after tags have been added/removed/cleared
    if action in ['post_add', 'post_remove', 'post_clear']:
        if instance.status == 'published':
            index_post(instance)