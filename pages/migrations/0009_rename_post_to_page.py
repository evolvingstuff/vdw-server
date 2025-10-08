from django.db import migrations, models


class Migration(migrations.Migration):
    app_label = 'posts'

    dependencies = [
        ('posts', '0008_remove_tag_model'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Post',
            new_name='Page',
        ),
        migrations.AlterModelOptions(
            name='page',
            options={
                'ordering': ['-created_date'],
                'verbose_name': 'Page',
                'verbose_name_plural': 'Pages',
            },
        ),
        migrations.AlterModelTable(
            name='page',
            table='posts_post',
        ),
        migrations.AlterField(
            model_name='page',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='pages', to='tags.tag'),
        ),
        migrations.AlterField(
            model_name='page',
            name='derived_tags',
            field=models.ManyToManyField(blank=True, editable=False, related_name='derived_pages', to='tags.tag'),
        ),
    ]
