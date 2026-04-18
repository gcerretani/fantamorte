from django.db import migrations


BONUS_TYPES = [
    {'name': 'Premio Nobel', 'points': 30, 'detection_method': 'wikidata',
     'wikidata_property': 'P3188', 'wikidata_value': '', 'age_formula': '', 'ordering': 1},
    {'name': 'Premio Oscar', 'points': 30, 'detection_method': 'wikidata',
     'wikidata_property': 'P166', 'wikidata_value': 'Q19020', 'age_formula': '', 'ordering': 2},
    {'name': 'Campione olimpico', 'points': 20, 'detection_method': 'wikidata',
     'wikidata_property': 'P166', 'wikidata_value': 'Q27020041', 'age_formula': '', 'ordering': 3},
    {'name': 'Morte giovane (under 50)', 'points': 20, 'detection_method': 'age',
     'wikidata_property': '', 'wikidata_value': '', 'age_formula': 'age < 50', 'ordering': 4},
    {'name': 'Morte violenta', 'points': 25, 'detection_method': 'manual',
     'wikidata_property': '', 'wikidata_value': '', 'age_formula': '', 'ordering': 5},
    {'name': 'Morte in diretta TV', 'points': 40, 'detection_method': 'manual',
     'wikidata_property': '', 'wikidata_value': '', 'age_formula': '', 'ordering': 6},
    {'name': "Prima morte dell'anno", 'points': 50, 'detection_method': 'manual',
     'wikidata_property': '', 'wikidata_value': '', 'age_formula': '', 'ordering': 7},
]


def seed_bonus_types(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    BonusType.objects.bulk_create([BonusType(**b) for b in BONUS_TYPES])


def unseed_bonus_types(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    BonusType.objects.filter(name__in=[b['name'] for b in BONUS_TYPES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_bonus_types, unseed_bonus_types),
    ]
