"""Assegna i bonus 'Primo morto' e 'Ultimo morto' della lega.

`first` viene tipicamente assegnato al primo decesso confermato della lega.
`last` viene assegnato a fine lega (chiusura del periodo di gioco).

Uso:
    python manage.py award_first_last_death --league <slug> --first
    python manage.py award_first_last_death --league <slug> --last
    python manage.py award_first_last_death --league <slug> --first --last
"""
from django.core.management.base import BaseCommand
from game.models import BonusType, Death, DeathBonus, League, LeagueBonus


class Command(BaseCommand):
    help = 'Assegna bonus primo/ultimo morto della lega'

    def add_arguments(self, parser):
        parser.add_argument('--league', type=str, required=True, help='Slug della lega')
        parser.add_argument('--first', action='store_true')
        parser.add_argument('--last', action='store_true')

    def handle(self, *args, **opts):
        if not (opts['first'] or opts['last']):
            self.stderr.write('Specifica almeno --first o --last')
            return

        league = League.objects.filter(slug=opts['league']).first()
        if league is None:
            self.stderr.write(f'Lega "{opts["league"]}" non trovata.')
            return

        confirmed = Death.objects.filter(
            is_confirmed=True,
            death_date__gte=league.start_date,
            death_date__lte=league.end_date,
        ).order_by('death_date')
        if not confirmed.exists():
            self.stdout.write('Nessun decesso confermato in questa lega.')
            return

        for flag, method in [('first', BonusType.DETECTION_FIRST_DEATH),
                             ('last', BonusType.DETECTION_LAST_DEATH)]:
            if not opts[flag]:
                continue
            lb = LeagueBonus.objects.filter(
                league=league, is_active=True, bonus_type__detection_method=method,
            ).select_related('bonus_type').first()
            if lb is None:
                self.stdout.write(self.style.WARNING(f'Lega senza bonus "{method}" attivo, salto.'))
                continue
            death = confirmed.first() if flag == 'first' else confirmed.last()
            _, created = DeathBonus.objects.get_or_create(
                death=death, bonus_type=lb.bonus_type,
                defaults={'points_awarded': lb.compute_points(age=death.death_age),
                          'is_auto_detected': True},
            )
            self.stdout.write(self.style.SUCCESS(
                f'{flag.capitalize()} ({league.name}): {death.person.name_it} '
                f'({"creato" if created else "già presente"})'
            ))
