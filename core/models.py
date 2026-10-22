from django.db import models
import re

from helper_functions.markdown import render_markdown


def derive_content_fields(content_md: str) -> tuple[str, str, int]:
    """(content_html, content_text, character_count) for the given markdown.

    Used by ContentBase.save and by data migrations that re-render stored pages.
    """
    content_html = render_markdown(content_md)
    # Plain text for search: drop tags, collapse whitespace
    content_text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', content_html)).strip()
    return content_html, content_text, len(content_text)


class ContentBase(models.Model):
    """Abstract base class for content with markdown editing functionality"""
    content_md = models.TextField(verbose_name="Content (Markdown)")
    content_html = models.TextField(editable=False)
    content_text = models.TextField(editable=False)
    character_count = models.IntegerField(default=0, help_text="Number of non-HTML characters in content")

    def save(self, *args, **kwargs):
        # Markdown -> HTML, plain text for search, and non-HTML character count
        self.content_html, self.content_text, self.character_count = derive_content_fields(self.content_md)

        super().save(*args, **kwargs)

    class Meta:
        abstract = True
