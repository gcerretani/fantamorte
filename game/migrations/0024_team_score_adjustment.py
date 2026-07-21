from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0023_bonustype_wikidata_value_multi_qid'),
    ]

    operations = [
        migrations.AddField(
            model_name='team',
            name='score_adjustment',
            field=models.IntegerField(
                default=0,
                help_text='Aggiustamento manuale del punteggio (anche negativo, es. penalità '
                          'per formazione in ritardo). Sommato al totale della squadra.',
            ),
        ),
        migrations.AddField(
            model_name='team',
            name='score_adjustment_reason',
            field=models.CharField(
                blank=True,
                help_text="Motivazione dell'aggiustamento manuale (mostrata nel dettaglio squadra).",
                max_length=200,
            ),
        ),
    ]
