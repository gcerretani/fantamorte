"""Corregge il valore Wikidata del bonus "Senatore a vita".

Q3373168 non è la carica corretta per P39 (position held): il valore giusto
è Q826589 ("senatore a vita" della Repubblica Italiana).
"""
from django.db import migrations

OLD_VALUE = 'Q3373168'
NEW_VALUE = 'Q826589'


def forwards(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    BonusType.objects.filter(
        name='Senatore a vita', wikidata_property='P39', wikidata_value=OLD_VALUE,
    ).update(wikidata_value=NEW_VALUE)


def backwards(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    BonusType.objects.filter(
        name='Senatore a vita', wikidata_property='P39', wikidata_value=NEW_VALUE,
    ).update(wikidata_value=OLD_VALUE)


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0016_rename_bonus_and_clean_descriptions'),
    ]
    operations = [
        migrations.RunPython(forwards, backwards),
    ]
