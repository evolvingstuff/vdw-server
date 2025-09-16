from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from core.models import ContentBase
from tags.models import Tag


class Post(ContentBase):
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
    tags = models.ManyToManyField(Tag, related_name='posts', blank=True)
    derived_tags = models.ManyToManyField(Tag, related_name='derived_posts', blank=True, editable=False)
    
    # SEO/Display
    meta_description = models.TextField(blank=True)
    
    # Internal notes (not displayed on frontend)
    notes = models.TextField(blank=True, help_text="Internal notes about this post (not displayed publicly)")
    
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
            while Post.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1

        # Call parent save (ContentBase) which handles markdown processing
        super().save(*args, **kwargs)

        # Copy tags to derived_tags (for now, until ontology is implemented)
        if self.pk:  # Only if the post has been saved
            self.derived_tags.set(self.tags.all())
    
    def __str__(self):
        return self.title
    
    class Meta:
        ordering = ['-created_date']

