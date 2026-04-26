from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0007_seed_leagues_from_seasons'),
    ]

    operations = [
        migrations.CreateModel(
            name='SiteConfiguration',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('wikidata_refresh_interval_hours', models.PositiveIntegerField(
                    default=24,
                    verbose_name='Intervallo aggiornamento Wikidata (ore)',
                    help_text=(
                        'Ore minime tra un controllo automatico Wikidata e il successivo '
                        'per ciascun concorrente vivente. Il cron può girare più spesso: '
                        'il sistema salterà le persone controllate di recente.'
                    ),
                )),
            ],
            options={
                'verbose_name': 'Configurazione sito',
                'verbose_name_plural': 'Configurazione sito',
            },
        ),
    ]
