from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.core.exceptions import ValidationError
from core.models import ContentBase


class SitePage(ContentBase):
    PAGE_TYPES = [
        ('homepage', 'Homepage'),
        ('about', 'About'),
        ('contact', 'Contact'),
        ('custom', 'Custom Page'),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True, max_length=200)
    page_type = models.CharField(max_length=20, choices=PAGE_TYPES, default='custom')
    is_published = models.BooleanField(default=True)
    meta_description = models.TextField(blank=True, help_text="SEO meta description")
    modified_date = models.DateTimeField(auto_now=True)
    public_modified_date = models.DateTimeField(default=timezone.now, editable=False)

    def save(self, *args, update_public_modified_date=True, **kwargs):
        assert isinstance(update_public_modified_date, bool), (
            "update_public_modified_date must be a bool"
        )

        # Enforce homepage singleton
        if self.page_type == 'homepage':
            # Check for existing homepage
            existing = SitePage.objects.filter(page_type='homepage').exclude(pk=self.pk).first()
            if existing:
                raise ValidationError("A homepage already exists")
            # Force homepage slug
            self.slug = 'home'

        # Auto-generate slug from title if not provided
        if not self.slug and self.page_type != 'homepage':
            self.slug = slugify(self.title)
            # Ensure uniqueness
            original_slug = self.slug
            counter = 1
            while SitePage.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1

        if update_public_modified_date:
            self.public_modified_date = timezone.now()

        # Call parent save (ContentBase) which handles markdown processing
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        if self.page_type == 'homepage':
            return '/'
        return f'/{self.slug}/'

    def __str__(self):
        return f"{self.title} ({self.get_page_type_display()})"

    class Meta:
        ordering = ['page_type', 'title']
        db_table = 'pages_page'
        constraints = [
            models.UniqueConstraint(
                fields=['page_type'],
                condition=models.Q(page_type='homepage'),
                name='unique_homepage'
            )
        ]
        verbose_name = 'Site page'
        verbose_name_plural = 'Site pages'
