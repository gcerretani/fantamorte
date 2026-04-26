"""Crea una League per ogni Season esistente e migra i Team."""
from datetime import date
from django.db import migrations
from django.utils.text import slugify


def forward(apps, schema_editor):
    Season = apps.get_model('game', 'Season')
    League = apps.get_model('game', 'League')
    Team = apps.get_model('game', 'Team')
    BonusType = apps.get_model('game', 'BonusType')
    LeagueBonus = apps.get_model('game', 'LeagueBonus')
    LeagueMembership = apps.get_model('game', 'LeagueMembership')
    User = apps.get_model('auth', 'User')

    # Owner di default: superuser più vecchio, altrimenti il primo utente
    default_owner = User.objects.filter(is_superuser=True).order_by('id').first()
    if default_owner is None:
        default_owner = User.objects.order_by('id').first()

    for season in Season.objects.all():
        teams_for_season = Team.objects.filter(season=season)
        if not teams_for_season.exists() and not season.is_active:
            continue
        owner = default_owner
        if owner is None:
            # Nessun utente: skippiamo silenziosamente. La lega verrà creata quando ce ne sarà bisogno.
            continue

        slug = slugify(f'stagione-{season.year}')
        league, _ = League.objects.get_or_create(
            slug=slug,
            defaults={
                'name': f'Stagione {season.year}',
                'description': season.notes or 'Lega legacy convertita dalla stagione.',
                'owner': owner,
                'visibility': 'public',
                'start_date': date(season.year, 1, 1),
                'end_date': date(season.year, 12, 31),
                'registration_opens': season.registration_opens,
                'registration_closes': season.registration_closes,
                'substitution_deadline_days': getattr(season, 'substitution_deadline_days', 7),
            },
        )

        # Attiva tutti i bonus_type esistenti come default della lega
        for bt in BonusType.objects.filter(is_active=True):
            LeagueBonus.objects.get_or_create(
                league=league, bonus_type=bt,
                defaults={'is_active': True},
            )

        # Owner come membership
        LeagueMembership.objects.get_or_create(
            league=league, user=owner, defaults={'role': 'owner'},
        )

        # Sposta i Team esistenti
        for team in teams_for_season:
            if team.league_id is None:
                team.league = league
                team.save(update_fields=['league'])
            LeagueMembership.objects.get_or_create(
                league=league, user_id=team.manager_id, defaults={'role': 'member'},
            )


def backward(apps, schema_editor):
    # Reversibile: rimuovi solo le leghe auto-generate
    League = apps.get_model('game', 'League')
    Team = apps.get_model('game', 'Team')
    Team.objects.filter(league__slug__startswith='stagione-').update(league=None)
    League.objects.filter(slug__startswith='stagione-').delete()


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0006_alter_team_season_league_alter_team_unique_together_and_more'),
    ]
    operations = [
        migrations.RunPython(forward, backward),
    ]
