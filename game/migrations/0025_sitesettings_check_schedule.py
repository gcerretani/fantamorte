from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0024_team_score_adjustment'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='wikidata_check_schedule_hours',
            field=models.PositiveIntegerField(
                default=1,
                help_text='Ogni quante ore gira lo scheduler dei controlli (deve combaciare con '
                          'la cadenza reale del cron/compose). Usato per dimensionare la fetta di '
                          'giocatori controllati a ogni run e distribuire il carico su Wikidata.',
            ),
        ),
        migrations.AlterField(
            model_name='sitesettings',
            name='wikidata_check_interval_hours',
            field=models.PositiveIntegerField(
                default=24,
                help_text='Periodo-obiettivo entro cui ogni giocatore viene ricontrollato su '
                          'Wikidata. I controlli sono distribuiti sui run dello scheduler.',
            ),
        ),
    ]
