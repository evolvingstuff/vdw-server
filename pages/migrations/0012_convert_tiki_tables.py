"""Phase 2 of the table fix (see docs/TABLES-FIX.md).

- Rewrites leftover Tiki tables in page markdown as markdown tables, saving each page's
  original markdown in PageContentBackup first.
- Re-renders content_html/content_text for every page whose tables render differently
  (Tiki tables, tables inside HTML boxes, tables missing blank lines).

Uses QuerySet.update(), so modified dates, "Most Recent" and save signals are untouched.
Reversible: the reverse step restores the backed-up markdown and re-renders those pages.
"""

from django.db import migrations, models

from core.models import derive_content_fields
from helper_functions.tables import convert_tiki_tables, prepare_tables_for_rendering

BACKUP_REASON = 'tiki-table-conversion (migration 0012)'


def convert_tables(apps, schema_editor):
    Page = apps.get_model('posts', 'Page')
    Backup = apps.get_model('posts', 'PageContentBackup')

    candidate_ids = list(Page.objects.filter(content_md__contains='|').values_list('id', flat=True))
    converted = rerendered = 0
    for page_id in candidate_ids:
        md = Page.objects.filter(pk=page_id).values_list('content_md', flat=True).first() or ''
        new_md, _reports = convert_tiki_tables(md)
        markdown_changed = new_md != md
        if not markdown_changed and prepare_tables_for_rendering(md) == md.replace('\r\n', '\n'):
            continue  # this page's tables already render the same way

        if markdown_changed:
            Backup.objects.create(page_id=page_id, content_md=md, reason=BACKUP_REASON)
            converted += 1
        content_html, content_text, character_count = derive_content_fields(new_md)
        Page.objects.filter(pk=page_id).update(
            content_md=new_md,
            content_html=content_html,
            content_text=content_text,
            character_count=character_count,
        )
        rerendered += 1
    print(f"\n  Table fix: {converted} pages had Tiki tables converted; {rerendered} pages re-rendered")


def restore_tables(apps, schema_editor):
    Page = apps.get_model('posts', 'Page')
    Backup = apps.get_model('posts', 'PageContentBackup')

    restored = 0
    for backup in Backup.objects.filter(reason=BACKUP_REASON).order_by('id'):
        content_html, content_text, character_count = derive_content_fields(backup.content_md)
        restored += Page.objects.filter(pk=backup.page_id).update(
            content_md=backup.content_md,
            content_html=content_html,
            content_text=content_text,
            character_count=character_count,
        )
    Backup.objects.filter(reason=BACKUP_REASON).delete()
    print(f"\n  Table fix reverted: original markdown restored on {restored} pages")


class Migration(migrations.Migration):

    dependencies = [
        ('posts', '0011_page_status_recent_cover'),
    ]

    operations = [
        migrations.CreateModel(
            name='PageContentBackup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('page_id', models.IntegerField(db_index=True)),
                ('content_md', models.TextField()),
                ('reason', models.CharField(max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={'db_table': 'posts_page_content_backup'},
        ),
        migrations.RunPython(convert_tables, restore_tables),
    ]
