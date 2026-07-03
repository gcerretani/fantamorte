"""Assegna i bonus 'Primo morto' e 'Ultimo morto' della lega.

`first` viene tipicamente assegnato al primo decesso confermato della lega.
`last` viene assegnato a fine lega (chiusura del periodo di gioco).

Uso:
    python manage.py award_first_last_death --league <slug> --first
    python manage.py award_first_last_death --league <slug> --last
    python manage.py award_first_last_death --league <slug> --first --last

Senza `--league` (modalità scheduler) elabora tutte le leghe eleggibili:
`--first` sulle leghe in corso, `--last` sulle leghe concluse. Il comando è
idempotente (get_or_create), quindi può girare quotidianamente via cron.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from game.models import BonusType, Death, DeathBonus, League, LeagueBonus


class Command(BaseCommand):
    help = 'Assegna bonus primo/ultimo morto della lega'

    def add_arguments(self, parser):
        parser.add_argument(
            '--league', type=str,
            help='Slug della lega (senza: tutte le leghe eleggibili per flag)',
        )
        parser.add_argument('--first', action='store_true')
        parser.add_argument('--last', action='store_true')

    def handle(self, *args, **opts):
        if not (opts['first'] or opts['last']):
            self.stderr.write('Specifica almeno --first o --last')
            return

        slug = opts.get('league')
        today = timezone.now().date()

        for flag, method in [('first', BonusType.DETECTION_FIRST_DEATH),
                             ('last', BonusType.DETECTION_LAST_DEATH)]:
            if not opts[flag]:
                continue

            if slug:
                leagues = League.objects.filter(slug=slug)
                if not leagues.exists():
                    self.stderr.write(f'Lega "{slug}" non trovata.')
                    return
            elif flag == 'first':
                # Leghe in corso: il primo decesso può già essere avvenuto.
                leagues = League.objects.filter(start_date__lte=today, end_date__gte=today)
            else:
                # Leghe concluse: l'ultimo decesso è definitivo.
                leagues = League.objects.filter(end_date__lt=today)

            for league in leagues:
                self._award(league, flag, method)

    def _award(self, league, flag, method):
        lb = LeagueBonus.objects.filter(
            league=league, is_active=True, bonus_type__detection_method=method,
        ).select_related('bonus_type').first()
        if lb is None:
            self.stdout.write(self.style.WARNING(
                f'{league.name}: senza bonus "{method}" attivo, salto.'
            ))
            return

        confirmed = Death.objects.filter(
            is_confirmed=True,
            death_date__gte=league.start_date,
            death_date__lte=league.end_date,
        ).order_by('death_date')
        if not confirmed.exists():
            self.stdout.write(f'{league.name}: nessun decesso confermato, salto.')
            return

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
