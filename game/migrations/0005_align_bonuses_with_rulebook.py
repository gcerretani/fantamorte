"""Allinea i tipi di bonus al regolamento attuale di FantaMorte."""
from django.db import migrations


# Stato target dei tipi di bonus secondo il regolamento. Solo bonus con
# detection automatica (wikidata/age) o speciale (original, first/last death):
# i bonus manuali non hanno un default di sistema, ogni lega crea i propri
# dal pannello admin.
TARGET = [
    {
        'name': 'Premio Nobel', 'points': 20, 'detection_method': 'wikidata',
        'wikidata_property': 'P166', 'wikidata_value': 'Q7191',
        'description': '20 punti per la morte di un premio Nobel.', 'ordering': 10,
    },
    {
        'name': 'Premio Oscar', 'points': 20, 'detection_method': 'wikidata',
        'wikidata_property': 'P166', 'wikidata_value': 'Q19020',
        'description': '20 punti per la morte di un premio Oscar.', 'ordering': 11,
    },
    {
        'name': 'Senatore a vita', 'points': 20, 'detection_method': 'wikidata',
        'wikidata_property': 'P39', 'wikidata_value': 'Q826589',
        'description': '20 punti per la morte di un senatore a vita italiano.', 'ordering': 13,
    },
    {
        'name': 'Morte under 60', 'points': 0, 'points_formula': '3*(60-age)',
        'detection_method': 'age', 'age_formula': 'age < 60',
        'description': '3 punti per ogni anno sotto i 60: N = 3·(60−età).', 'ordering': 18,
    },
    {
        'name': 'Giocata originale', 'points': 30, 'detection_method': 'original',
        'description': '30 punti se la persona era stata scelta da un solo manager '
                       'all\'inizio della stagione.', 'ordering': 19,
    },
    {
        'name': "Primo morto della stagione", 'points': 50, 'detection_method': 'first_death',
        'description': '50 punti per il primo decesso della stagione.', 'ordering': 20,
    },
    {
        'name': "Ultimo morto della stagione", 'points': 50, 'detection_method': 'last_death',
        'description': '50 punti per l\'ultimo decesso della stagione (assegnato a fine anno).',
        'ordering': 21,
    },
]

# Vecchi nomi che vogliamo rimuovere o rinominare se esistono.
LEGACY_NAMES = [
    'Morte giovane (under 50)',
    "Prima morte dell'anno",
]


def upsert_bonuses(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    # Rimuovi i bonus legacy se non hanno DeathBonus collegati.
    for legacy in LEGACY_NAMES:
        BonusType.objects.filter(name=legacy, awarded__isnull=True).delete()
    for spec in TARGET:
        defaults = {
            'description': spec.get('description', ''),
            'points': spec['points'],
            'points_formula': spec.get('points_formula', ''),
            'detection_method': spec['detection_method'],
            'wikidata_property': spec.get('wikidata_property', ''),
            'wikidata_value': spec.get('wikidata_value', ''),
            'age_formula': spec.get('age_formula', ''),
            'is_active': True,
            'ordering': spec['ordering'],
        }
        BonusType.objects.update_or_create(name=spec['name'], defaults=defaults)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0004_bonustype_points_formula_teammember_is_original_and_more'),
    ]
    operations = [
        migrations.RunPython(upsert_bonuses, noop),
    ]
