from django.db import models
import markdown2
import re


class ContentBase(models.Model):
    """Abstract base class for content with markdown editing functionality"""
    content_md = models.TextField(verbose_name="Content (Markdown)")
    content_html = models.TextField(editable=False)
    content_text = models.TextField(editable=False)
    character_count = models.IntegerField(default=0, help_text="Number of non-HTML characters in content")

    def save(self, *args, **kwargs):
        # Process markdown to HTML
        self.content_html = markdown2.markdown(
            self.content_md,
            extras=['fenced-code-blocks', 'tables', 'strike', 'footnotes']
        )

        # Extract plain text for search
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', self.content_html)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        self.content_text = text.strip()

        # Calculate character count (non-HTML characters)
        self.character_count = len(self.content_text)

        super().save(*args, **kwargs)

    class Meta:
        abstract = True
