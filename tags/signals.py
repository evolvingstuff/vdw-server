from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from pages.models import Page

from .models import Tag
from .services import mark_topics_changed


@receiver(post_save, sender=Page)
@receiver(post_delete, sender=Page)
@receiver(post_save, sender=Tag)
@receiver(post_delete, sender=Tag)
def topics_changed_on_save(sender, **kwargs):
    mark_topics_changed()


@receiver(m2m_changed, sender=Page.tags.through)
@receiver(m2m_changed, sender=Page.derived_tags.through)
def topics_changed_on_tagging(sender, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        mark_topics_changed()
