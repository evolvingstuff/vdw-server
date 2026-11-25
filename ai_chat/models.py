"""Settings for the on-page AI chat, edited in the admin (one record)."""

import time

from django.db import models

SETTINGS_CACHE_SECONDS = 30  # each server process re-reads the record this often
_cached = None
_cached_at = 0.0


class ChatSettings(models.Model):
    WIKI_ONLY = 'wiki_only'
    WIKI_FIRST = 'wiki_first'
    OPEN = 'open'
    STRICTNESS_CHOICES = [
        (WIKI_ONLY, "Wiki only: answer only from VitaminDWiki pages"),
        (WIKI_FIRST, "Wiki first: may add general knowledge, clearly marked"),
        (OPEN, "Open: general knowledge freely, wiki claims linked"),
    ]

    enabled = models.BooleanField(
        default=True,
        help_text='Show the "Ask AI" button on wiki pages. Changes take up to half a minute to show.',
    )
    strictness = models.CharField(
        'how strictly it sticks to the wiki',
        max_length=20,
        choices=STRICTNESS_CHOICES,
        default=WIKI_FIRST,
    )
    extra_instructions = models.TextField(
        blank=True,
        help_text=(
            'Optional. Added to the instructions the AI assistant gets, e.g. how to treat official '
            'vitamin D guidelines. Plain text.'
        ),
    )

    class Meta:
        verbose_name = 'AI chat settings'
        verbose_name_plural = 'AI chat settings'

    def __str__(self):
        return 'AI chat settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        clear_chat_settings_cache()


def get_chat_settings() -> ChatSettings:
    """The settings record (created with defaults on first use), cached briefly per process."""
    global _cached, _cached_at
    if _cached is None or time.monotonic() - _cached_at > SETTINGS_CACHE_SECONDS:
        _cached, _ = ChatSettings.objects.get_or_create(pk=1)
        _cached_at = time.monotonic()
    return _cached


def clear_chat_settings_cache() -> None:
    global _cached
    _cached = None
