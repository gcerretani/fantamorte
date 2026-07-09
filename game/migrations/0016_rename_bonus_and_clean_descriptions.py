"""Rinomina "Morte under 60" in "Morte giovane" e ripulisce le descrizioni.

Le descrizioni dei bonus di sistema non devono contenere i punti hardcoded:
i punti effettivi dipendono dalla lega (override in LeagueBonus) e vengono
mostrati dai template leggendo i campi reali (points/points_formula). La
descrizione resta solo testo esplicativo del criterio.
"""
from django.db import migrations


# name → (nuovo nome oppure None, nuova descrizione)
UPDATES = {
    'Premio Nobel': (None, 'La persona ha ricevuto un premio Nobel, in qualsiasi disciplina.'),
    'Premio Oscar': (None, 'La persona ha ricevuto un premio Oscar.'),
    'Senatore a vita': (None, 'La persona è stata senatore a vita italiano.'),
    'Morte under 60': (
        'Morte giovane',
        'Punti proporzionali agli anni mancanti alla soglia dei 60.',
    ),
    'Giocata originale': (
        None,
        'La persona era stata scelta da un solo manager all\'inizio della stagione.',
    ),
    'Primo morto della stagione': (
        None,
        'Primo decesso confermato nel periodo di gioco della lega.',
    ),
    'Ultimo morto della stagione': (
        None,
        'Ultimo decesso confermato nel periodo di gioco della lega '
        '(assegnato solo a lega conclusa).',
    ),
}


def apply_updates(apps, schema_editor):
    BonusType = apps.get_model('game', 'BonusType')
    for old_name, (new_name, description) in UPDATES.items():
        qs = BonusType.objects.filter(name=old_name, league__isnull=True)
        if new_name and BonusType.objects.filter(name=new_name, league__isnull=True).exists():
            new_name = None  # il nome target esiste già: aggiorna solo la descrizione
        qs.update(description=description, **({'name': new_name} if new_name else {}))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0015_bonustype_league_league_max_total_age_and_more'),
    ]
    operations = [
        migrations.RunPython(apply_updates, noop),
    ]
