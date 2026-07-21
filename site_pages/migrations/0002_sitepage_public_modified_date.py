from django.db import migrations, models
from django.db.models import F
import django.utils.timezone


def copy_public_modified_dates(apps, schema_editor):
    site_page_model = apps.get_model("pages", "SitePage")
    site_page_model.objects.update(public_modified_date=F("modified_date"))


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitepage",
            name="public_modified_date",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
            ),
        ),
        migrations.RunPython(copy_public_modified_dates, migrations.RunPython.noop),
    ]
