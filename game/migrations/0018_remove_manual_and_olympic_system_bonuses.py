"""Rimuove dai bonus di sistema quelli manuali e "Campione olimpico".

"Campione olimpico" (P166=Q27020041) si è rivelato troppo inaffidabile:
la maggior parte delle medaglie olimpiche recenti non è modellata su
Wikidata con "award received" (P166) ma con "participant in" (P1344) +
qualificatore "ranking", quindi la detection automatica manca la maggior
parte dei casi reali (vedi i test in wikidata_api/tests.py).

I bonus manuali di sistema (Capo di stato/governo/parlamento, Morte
violenta, Morte in diretta TV, Morte per COVID-19) vengono rimossi
dall'elenco predefinito: gli admin di lega che li vogliono possono
crearli come bonus manuali personalizzati dal pannello admin della
propria lega.

Le righe vengono cancellate solo se non hanno DeathBonus collegati
(come nel pattern già usato in 0005 per i bonus legacy); se una lega li
ha già assegnati a un decesso reale, vengono invece solo disattivati
(`is_active=False`) per non rompere lo storico dei punteggi.
"""
from django.db import migrations


# name -> (points, points_formula, detection_method, wikidata_property,
#          wikidata_value, description, ordering)
REMOVED = {
    'Campione olimpico': (
        20, '', 'wikidata', 'P166', 'Q27020041',
        'La persona è stata campione olimpico.', 12,
    ),
    'Capo di stato/governo/parlamento': (
        30, '', 'manual', '', '',
        'Capo di stato, di governo o del parlamento di un qualsiasi stato, '
        'in carica al momento della morte.', 14,
    ),
    'Morte violenta': (
        20, '', 'manual', '', '',
        'Decesso per causa violenta.', 15,
    ),
    'Morte in diretta TV': (
        30, '', 'manual', '', '',
        'Decesso avvenuto in diretta televisiva.', 16,
    ),
    'Morte per COVID-19': (
        100, '', 'manual', '', '',
        'Decesso causato dal COVID-19.', 17,
    ),
}


def remove_bonuses(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    for name in REMOVED:
        qs = BonusType.objects.filter(name=name, league__isnull=True)
        qs.filter(awarded__isnull=True).delete()
        qs.filter(awarded__isnull=False).update(is_active=False)


def restore_bonuses(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    for name, (points, points_formula, detection_method, prop, value, description, ordering) in REMOVED.items():
        BonusType.objects.update_or_create(
            name=name, league=None,
            defaults={
                'points': points,
                'points_formula': points_formula,
                'detection_method': detection_method,
                'wikidata_property': prop,
                'wikidata_value': value,
                'description': description,
                'is_active': True,
                'ordering': ordering,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0017_fix_senatore_a_vita_wikidata_value'),
    ]
    operations = [
        migrations.RunPython(remove_bonuses, restore_bonuses),
    ]
