"""Assegna i bonus 'Primo morto' e 'Ultimo morto' della stagione.

`first` viene tipicamente assegnato al primo decesso confermato.
`last` viene assegnato a fine stagione (chiusura dell'anno).

Uso:
    python manage.py award_first_last_death --first
    python manage.py award_first_last_death --last
    python manage.py award_first_last_death --first --last
"""
from django.core.management.base import BaseCommand
from game.models import BonusType, Death, DeathBonus, Season


class Command(BaseCommand):
    help = 'Assegna bonus primo/ultimo morto della stagione'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=None)
        parser.add_argument('--first', action='store_true')
        parser.add_argument('--last', action='store_true')

    def handle(self, *args, **opts):
        if not (opts['first'] or opts['last']):
            self.stderr.write('Specifica almeno --first o --last')
            return
        if opts['year']:
            season = Season.objects.filter(year=opts['year']).first()
        else:
            season = Season.objects.filter(is_active=True).first()
        if not season:
            self.stderr.write('Nessuna stagione trovata.')
            return

        confirmed = Death.objects.filter(season=season, is_confirmed=True).order_by('death_date')
        if not confirmed.exists():
            self.stdout.write('Nessun decesso confermato.')
            return

        if opts['first']:
            bt = BonusType.objects.filter(detection_method=BonusType.DETECTION_FIRST_DEATH, is_active=True).first()
            if bt:
                death = confirmed.first()
                _, created = DeathBonus.objects.get_or_create(
                    death=death, bonus_type=bt,
                    defaults={'points_awarded': bt.points, 'is_auto_detected': True},
                )
                self.stdout.write(self.style.SUCCESS(
                    f'Primo morto: {death.person.name_it} ({"creato" if created else "già presente"})'
                ))

        if opts['last']:
            bt = BonusType.objects.filter(detection_method=BonusType.DETECTION_LAST_DEATH, is_active=True).first()
            if bt:
                death = confirmed.last()
                _, created = DeathBonus.objects.get_or_create(
                    death=death, bonus_type=bt,
                    defaults={'points_awarded': bt.points, 'is_auto_detected': True},
                )
                self.stdout.write(self.style.SUCCESS(
                    f'Ultimo morto: {death.person.name_it} ({"creato" if created else "già presente"})'
                ))
