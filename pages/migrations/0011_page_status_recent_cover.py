from django.db import migrations, models


class Migration(migrations.Migration):
    """Covering index so "Most Recent" and the topic index don't scan the ~1 GB table."""

    dependencies = [
        ('posts', '0010_page_public_modified_date'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='page',
            index=models.Index(
                fields=['status', 'public_modified_date', 'created_date', 'id', 'title', 'slug'],
                name='page_status_recent_cover',
            ),
        ),
    ]
