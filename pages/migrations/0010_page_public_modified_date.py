from django.db import migrations, models
from django.db.models import F
import django.utils.timezone


def copy_public_modified_dates(apps, schema_editor):
    page_model = apps.get_model("posts", "Page")
    page_model.objects.update(public_modified_date=F("modified_date"))


class Migration(migrations.Migration):
    dependencies = [
        ("posts", "0009_rename_post_to_page"),
    ]

    operations = [
        migrations.AddField(
            model_name="page",
            name="public_modified_date",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
            ),
        ),
        migrations.RunPython(copy_public_modified_dates, migrations.RunPython.noop),
    ]
