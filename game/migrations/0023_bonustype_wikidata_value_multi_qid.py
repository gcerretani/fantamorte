from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0022_wikipediaperson_image_url_length'),
    ]

    operations = [
        migrations.AlterField(
            model_name='bonustype',
            name='wikidata_value',
            field=models.CharField(
                blank=True,
                help_text='Uno o più QID separati da virgola (es. Q7191,Q47170). '
                          'Vuoto = qualsiasi valore della proprietà.',
                max_length=200,
            ),
        ),
    ]
