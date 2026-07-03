"""Elimina le righe DeathBonus di tipo primo/ultimo morto.

Questi bonus sono relativi alla singola lega e ora vengono calcolati
dinamicamente dallo scoring (game/scoring.py): le righe persistite erano
condivise tra tutte le leghe e creavano correlazioni indebite quando più
leghe includevano la stessa persona.
"""
from django.db import migrations


def purge_first_last_rows(apps, schema_editor):
    DeathBonus = apps.get_model('game', 'DeathBonus')
    DeathBonus.objects.filter(
        bonus_type__detection_method__in=['first_death', 'last_death'],
    ).delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0012_remove_death_season_remove_team_season_delete_season'),
    ]
    operations = [
        migrations.RunPython(purge_first_last_rows, noop),
    ]
