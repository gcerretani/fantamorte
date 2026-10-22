"""Nuova lega a partire da una esistente (#101).

Le stagioni non hanno uno status speciale: la "Fantamorte 2027" è una lega
nuova che riprende le regole della 2026. Si copia tutto ciò che è
*configurazione* (regole, lingue wiki, bonus attivi con gli override, bonus
personalizzati di lega) e niente di ciò che è *gioco* (squadre, iscritti,
decessi, assegnazioni manuali di bonus). Nome e date sono nuovi; il codice
invito pure.

I bonus personalizzati (`BonusType.league`) appartengono alla lega: vengono
**duplicati** sulla nuova, mai condivisi, così modificarli in una lega non
cambia i punti dell'altra.
"""
import secrets
from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from . import views
from .models import BonusType, League, LeagueBonus, LeagueMembership

# Campi di League copiati così come sono. Tutto il resto (nome, slug, date,
# owner, invite_code, is_locked, created_at) è della lega nuova.
RULE_FIELDS = (
    'description', 'visibility',
    'max_captains', 'max_non_captains', 'max_total_age', 'jolly_enabled',
    'secret_rosters_preseason', 'base_points', 'captain_multiplier',
    'jolly_multiplier', 'substitution_deadline_days', 'captain_succession',
    'search_wikipedia_langs',
)
BONUS_TYPE_FIELDS = (
    'name', 'description', 'points', 'points_formula', 'detection_method',
    'wikidata_property', 'wikidata_value', 'age_formula', 'is_active', 'ordering',
)
DATE_FIELDS = ('start_date', 'end_date', 'registration_opens', 'registration_closes')


def shift_year(d, years=1):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 febbraio
        return d.replace(year=d.year + years, day=28)


def suggest_name(name):
    """«Fantamorte 2026» → «Fantamorte 2027»; senza anno → «… (nuova edizione)»."""
    words = name.split()
    for i in range(len(words) - 1, -1, -1):
        if words[i].isdigit() and len(words[i]) == 4:
            words[i] = str(int(words[i]) + 1)
            candidate = ' '.join(words)
            if not League.objects.filter(name__iexact=candidate).exists():
                return candidate
            break
    return f'{name} (nuova edizione)'


def validate_dates(dates):
    """Stessi vincoli di update_rules in LeagueAdminView. Ritorna un errore o ''."""
    if dates['start_date'] > dates['end_date']:
        return 'La data di inizio deve precedere la fine.'
    if dates['registration_opens'] > dates['registration_closes']:
        return "L'apertura iscrizioni deve precedere la chiusura."
    if dates['registration_closes'] > dates['start_date']:
        return ('La chiusura delle iscrizioni non può essere successiva '
                "all'inizio del gioco: le squadre devono essere definitive "
                'quando i decessi iniziano a contare.')
    return ''


@transaction.atomic
def clone_league(source, *, name, owner, dates, visibility=None):
    league = League(
        name=name,
        slug=views._unique_slug(name),
        owner=owner,
        **{f: getattr(source, f) for f in RULE_FIELDS},
        **dates,
    )
    if visibility in dict(League.VISIBILITY_CHOICES):
        league.visibility = visibility
    league.invite_code = secrets.token_urlsafe(8) if league.visibility == League.VISIBILITY_PRIVATE else ''
    league.save()
    LeagueMembership.objects.create(league=league, user=owner, role=LeagueMembership.ROLE_OWNER)

    custom_map = {}
    for bt in source.custom_bonus_types.all():
        custom_map[bt.pk] = BonusType.objects.create(
            league=league, **{f: getattr(bt, f) for f in BONUS_TYPE_FIELDS})
    for lb in source.league_bonuses.select_related('bonus_type'):
        bonus_type = custom_map.get(lb.bonus_type_id, lb.bonus_type)
        LeagueBonus.objects.create(
            league=league, bonus_type=bonus_type, is_active=lb.is_active,
            override_points=lb.override_points, override_formula=lb.override_formula,
        )
    return league


def invite_url(league):
    if league.visibility == League.VISIBILITY_PRIVATE:
        return reverse('league_join', args=[league.slug]) + f'?code={league.invite_code}'
    return reverse('league_detail', args=[league.slug])


class LeagueCloneView(LoginRequiredMixin, View):
    template_name = 'game/league_clone.html'

    def _source(self, request, slug):
        source = get_object_or_404(League, slug=slug)
        # Come il pannello admin: owner, admin di lega o staff.
        if not (source.is_admin(request.user) or request.user.is_staff):
            return source, False
        return source, True

    def _render(self, request, source, data):
        return render(request, self.template_name, {
            'league': source,
            'data': data,
            'is_admin': True,
            'invitable_count': source.memberships.exclude(user=request.user).count(),
            'custom_bonus_count': source.custom_bonus_types.count(),
            'active_bonus_count': source.league_bonuses.filter(is_active=True).count(),
        })

    def get(self, request, slug):
        source, ok = self._source(request, slug)
        if not ok:
            return HttpResponseForbidden('Permesso negato.')
        data = {f: shift_year(getattr(source, f)).isoformat() for f in DATE_FIELDS}
        data.update(name=suggest_name(source.name), visibility=source.visibility,
                    invite_members=True)
        return self._render(request, source, data)

    def post(self, request, slug):
        source, ok = self._source(request, slug)
        if not ok:
            return HttpResponseForbidden('Permesso negato.')
        data = {
            'name': request.POST.get('name', '').strip(),
            'visibility': request.POST.get('visibility', source.visibility),
            'invite_members': request.POST.get('invite_members') == '1',
            **{f: request.POST.get(f, '') for f in DATE_FIELDS},
        }
        error = ''
        if not data['name']:
            error = 'Il nome è obbligatorio.'
        elif League.objects.filter(name__iexact=data['name']).exists():
            error = 'Esiste già una lega con questo nome.'
        else:
            try:
                dates = {f: date.fromisoformat(data[f]) for f in DATE_FIELDS}
            except ValueError:
                error = 'Formato data non valido.'
            else:
                error = validate_dates(dates)
        if error:
            messages.error(request, error)
            return self._render(request, source, data)

        league = clone_league(source, name=data['name'], owner=request.user,
                              dates=dates, visibility=data['visibility'])
        invited = 0
        if data['invite_members']:
            from .notifications import notify_league_invite
            invited = notify_league_invite(source, league, inviter=request.user)
        msg = f'Lega «{league.name}» creata con le regole di «{source.name}».'
        if invited:
            msg += f' Invit{"o" if invited == 1 else "i"} inviat{"o" if invited == 1 else "i"} a {invited} iscritt{"o" if invited == 1 else "i"}.'
        messages.success(request, msg)
        return redirect('league_admin', slug=league.slug)

