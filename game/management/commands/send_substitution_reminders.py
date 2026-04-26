"""Invia reminder (push + email opzionale) ai manager con membri morti
non ancora sostituiti, quando la deadline si avvicina.

Pensato per girare una volta al giorno via cron. Idempotente: usa il
modello `SubstitutionReminder` come marker (unique_together su
team_member + threshold_days), così la stessa soglia non viene mai
notificata due volte per lo stesso membro.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone

from game.models import League, SubstitutionReminder, TeamMember


DEFAULT_THRESHOLDS = (3, 1)


class Command(BaseCommand):
    help = 'Invia reminder push/email per le sostituzioni in scadenza.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Non inviare e non scrivere il marker.')
        parser.add_argument('--league', type=str, default=None,
                            help='Slug di una lega specifica.')
        parser.add_argument('--thresholds', type=str, default=','.join(str(t) for t in DEFAULT_THRESHOLDS),
                            help='Soglie in giorni separate da virgola (default: "3,1").')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        slug = options.get('league')

        try:
            thresholds = sorted({int(x) for x in options['thresholds'].split(',') if x.strip()}, reverse=True)
        except ValueError:
            raise CommandError('--thresholds deve essere una lista di interi separati da virgola.')
        if not thresholds:
            raise CommandError('Devi specificare almeno una soglia.')

        leagues_qs = League.objects.all()
        if slug:
            leagues_qs = leagues_qs.filter(slug=slug)
        else:
            today = timezone.now().date()
            leagues_qs = leagues_qs.filter(start_date__lte=today, end_date__gte=today)
        league_ids = list(leagues_qs.values_list('id', flat=True))
        if not league_ids:
            self.stdout.write('Nessuna lega in corso trovata.')
            return

        members = TeamMember.objects.filter(
            team__league_id__in=league_ids,
            replaced_by__isnull=True,
            person__death__is_confirmed=True,
        ).select_related('person', 'person__death', 'team', 'team__league', 'team__manager')

        now = timezone.now()
        sent_count = 0
        skipped_count = 0
        for member in members:
            deadline = member.get_substitution_deadline()
            if deadline is None:
                continue
            seconds_left = (deadline - now).total_seconds()
            if seconds_left <= 0:
                continue

            # Trova la soglia "minima" attiva ora (giorni rimasti <= soglia)
            days_left_float = seconds_left / 86400
            applicable = [t for t in thresholds if days_left_float <= t]
            if not applicable:
                continue
            threshold = min(applicable)
            days_left = max(int(days_left_float), 1) if seconds_left < threshold * 86400 else threshold

            if SubstitutionReminder.objects.filter(team_member=member, threshold_days=threshold).exists():
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    f'[dry-run] T-{threshold}: {member.team.manager.username} '
                    f'/ {member.person.name_it} (~{days_left} giorni)'
                )
                sent_count += 1
                continue

            push_sent = False
            email_sent = False
            try:
                from game.push import send_substitution_reminder_push
                push_sent = send_substitution_reminder_push(member, days_left)
            except Exception as e:
                self.stderr.write(f'Errore push per member {member.pk}: {e}')

            try:
                from game.email import send_substitution_reminder_email
                email_sent = send_substitution_reminder_email(member, days_left)
            except Exception as e:
                self.stderr.write(f'Errore email per member {member.pk}: {e}')

            try:
                with transaction.atomic():
                    SubstitutionReminder.objects.create(
                        team_member=member,
                        threshold_days=threshold,
                        push_sent=push_sent,
                        email_sent=email_sent,
                    )
            except IntegrityError:
                # Race condition: un altro run ha già creato il marker
                skipped_count += 1
                continue

            sent_count += 1
            self.stdout.write(
                f'T-{threshold}: {member.team.manager.username} '
                f'/ {member.person.name_it} (~{days_left}g) push={push_sent} email={email_sent}'
            )

        self.stdout.write(self.style.SUCCESS(
            f'Reminder inviati: {sent_count} (skipped: {skipped_count})'
        ))
