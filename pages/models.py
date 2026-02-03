from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from core.models import ContentBase
from tags.models import Tag


def _title_slug_haystack(title: str) -> str:
    assert isinstance(title, str), f"title must be str, got {type(title)}"
    title_slug = slugify(title)
    if not title_slug:
        return ''
    return f"-{title_slug}-"


class Page(ContentBase):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ]

    # Core fields
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, blank=True, max_length=200)
    
    # Metadata
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')
    created_date = models.DateTimeField(default=timezone.now)
    modified_date = models.DateTimeField(auto_now=True)
    
    # Tag system with ontology support
    tags = models.ManyToManyField(Tag, related_name='pages', blank=True)
    derived_tags = models.ManyToManyField(Tag, related_name='derived_pages', blank=True, editable=False)
    
    # SEO/Display
    meta_description = models.TextField(blank=True)
    
    # Internal notes (not displayed on frontend)
    notes = models.TextField(blank=True, help_text="Internal notes about this page (not displayed publicly)")
    
    # Migration fields (not editable in admin)
    original_page_id = models.IntegerField(null=True, blank=True, editable=False)
    aliases = models.TextField(blank=True, editable=False, help_text="Old URLs for redirects, one per line")
    front_matter = models.TextField(blank=True, null=True, editable=False, help_text="Original frontmatter JSON for debugging")
    original_tiki = models.TextField(blank=True, null=True, editable=False, help_text="Original Tiki wiki markup for reference")
    redacted_count = models.IntegerField(default=0, help_text="Number of censored sections from Tiki conversion")
    
    def save(self, *args, **kwargs):
        # Auto-generate slug if not provided
        if not self.slug:
            self.slug = slugify(self.title)
            # Ensure uniqueness
            original_slug = self.slug
            counter = 1
            while Page.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1

        # Call parent save (ContentBase) which handles markdown processing
        super().save(*args, **kwargs)

        # Sync derived_tags (explicit tags + implied tags from title)
        if self.pk:  # Only if the page has been saved
            self.update_derived_tags()

    def update_derived_tags(self) -> None:
        assert self.pk, "Page must be saved before updating derived tags"

        explicit_tags = list(self.tags.all())
        explicit_ids = {tag.pk for tag in explicit_tags}
        title_haystack = _title_slug_haystack(self.title)

        if not title_haystack:
            self.derived_tags.set(explicit_tags)
            return

        implied_tags = []
        for tag in Tag.objects.only('id', 'slug'):
            if tag.pk in explicit_ids:
                continue
            if not tag.slug:
                continue
            if f"-{tag.slug}-" in title_haystack:
                implied_tags.append(tag)

        self.derived_tags.set([*explicit_tags, *implied_tags])
    
    def __str__(self):
        return self.title
    
    class Meta:
        ordering = ['-created_date']
        db_table = 'posts_post'
        verbose_name = 'Page'
        verbose_name_plural = 'Pages'
