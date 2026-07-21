from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0021_league_captain_succession_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='wikipediaperson',
            name='image_url',
            field=models.URLField(blank=True, max_length=1000),
        ),
    ]
