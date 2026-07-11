import csv
import hashlib
import json
import logging
import re
import secrets
from datetime import date, timedelta
from urllib.parse import unquote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import slugify
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, DetailView, View

from wikidata_api.client import WikidataClient

from . import person_sync, scoring
from .models import (
    MONTHS_IT, BonusType, Death, DeathBonus, League, LeagueBonus,
    LeagueMembership, PushSubscription, SiteSettings, Team, TeamMember,
    UserProfile, WikipediaPerson,
)


logger = logging.getLogger(__name__)


# ---------------- Dashboard utente ----------------

class HomeView(LoginRequiredMixin, TemplateView):
    template_name = 'game/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        my_memberships = (
            LeagueMembership.objects.filter(user=user)
            .select_related('league')
            .order_by('-league__start_date')
        )
        teams_by_league = {
            t.league_id: t
            for t in Team.objects.filter(manager=user, league__isnull=False)
        }
        my_leagues = []
        for m in my_memberships:
            team = teams_by_league.get(m.league_id)
            entry = {'league': m.league, 'role': m.role, 'team': team,
                     'score': None, 'next_deadline': None}
            if team:
                # La classifica è già cachata (scoring): costo ammortizzato.
                for row in scoring.compute_league_rankings(m.league):
                    if row['team'].pk == team.pk:
                        entry['score'] = row['score']
                        break
                # Prossima scadenza di sostituzione tra i membri morti attivi.
                deadlines = [
                    member.get_substitution_deadline()
                    for member in team.members.filter(
                        replaced_by=None, person__is_dead=True,
                    ).select_related('person')
                    if member.can_be_substituted()
                ]
                deadlines = [d for d in deadlines if d]
                if deadlines:
                    entry['next_deadline'] = min(deadlines)
            my_leagues.append(entry)
        ctx['my_leagues'] = my_leagues
        # Suggerimenti: leghe pubbliche di cui non sono membro
        member_ids = [m.league_id for m in my_memberships]
        ctx['suggested_leagues'] = (
            League.objects.filter(visibility=League.VISIBILITY_PUBLIC)
            .exclude(pk__in=member_ids)
            .order_by('-start_date')[:5]
        )
        return ctx


class StatsView(LoginRequiredMixin, TemplateView):
    """Statistiche cross-lega: storico personale + leaderboard all-time.

    La leaderboard aggrega solo le leghe visibili all'utente (pubbliche o di
    cui è membro), per non rivelare dati di leghe private altrui.
    """
    template_name = 'game/stats.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user

        member_league_ids = set(
            LeagueMembership.objects.filter(user=user).values_list('league_id', flat=True)
        )
        visible_leagues = [
            l for l in League.objects.all().order_by('-start_date')
            if l.visibility == League.VISIBILITY_PUBLIC or l.pk in member_league_ids
        ]

        my_history = []
        totals = {}  # manager_id -> aggregato all-time
        for league in visible_leagues:
            rankings = scoring.compute_league_rankings(league)
            for pos, entry in enumerate(rankings, start=1):
                team = entry['team']
                manager = team.manager
                agg = totals.setdefault(manager.pk, {
                    'manager': manager, 'points': 0, 'leagues': 0, 'wins': 0,
                })
                agg['points'] += entry['score']
                agg['leagues'] += 1
                if pos == 1 and entry['score'] > 0:
                    agg['wins'] += 1
                if manager.pk == user.pk:
                    my_history.append({
                        'league': league,
                        'team': team,
                        'score': entry['score'],
                        'position': pos,
                        'teams_count': len(rankings),
                    })

        all_time = sorted(totals.values(), key=lambda a: -a['points'])[:50]
        ctx['my_history'] = my_history
        ctx['all_time'] = all_time
        return ctx


# ---------------- League views ----------------

class LeagueListView(LoginRequiredMixin, TemplateView):
    template_name = 'game/league_list.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        public = League.objects.filter(visibility=League.VISIBILITY_PUBLIC)
        joined_ids = set(LeagueMembership.objects.filter(user=user).values_list('league_id', flat=True))
        ctx['public_leagues'] = public
        ctx['my_league_ids'] = joined_ids
        return ctx


class LeagueCreateView(LoginRequiredMixin, View):
    template_name = 'game/league_form.html'

    def get(self, request):
        return render(request, self.template_name, {'creating': True})

    def post(self, request):
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Il nome è obbligatorio.')
            return render(request, self.template_name, {'creating': True, 'data': request.POST})
        if League.objects.filter(name__iexact=name).exists():
            messages.error(request, 'Esiste già una lega con questo nome.')
            return render(request, self.template_name, {'creating': True, 'data': request.POST})

        visibility = request.POST.get('visibility', League.VISIBILITY_PUBLIC)
        if visibility not in dict(League.VISIBILITY_CHOICES):
            visibility = League.VISIBILITY_PUBLIC
        slug = _unique_slug(name)
        today = timezone.now().date()
        league = League.objects.create(
            name=name,
            slug=slug,
            owner=request.user,
            visibility=visibility,
            invite_code=secrets.token_urlsafe(8) if visibility == League.VISIBILITY_PRIVATE else '',
            start_date=today,
            end_date=today,
            registration_opens=today,
            registration_closes=today,
        )

        LeagueMembership.objects.create(league=league, user=request.user, role=LeagueMembership.ROLE_OWNER)
        for bt in BonusType.objects.filter(is_active=True, league__isnull=True):
            LeagueBonus.objects.create(league=league, bonus_type=bt, is_active=True)

        messages.success(request, f'Lega "{league.name}" creata! Configura ora calendario e regole.')
        return redirect('league_admin', slug=league.slug)


def _unique_slug(name):
    base = slugify(name) or 'lega'
    slug = base
    i = 2
    while League.objects.filter(slug=slug).exists():
        slug = f'{base}-{i}'
        i += 1
    return slug


class LeagueDetailView(LoginRequiredMixin, View):
    template_name = 'game/league_detail.html'

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            # Teaser per non-membri di lega privata: mostra solo il form
            # per il codice invito (eventualmente precompilato da ?code=).
            return render(request, 'game/league_join.html', {
                'league': league,
                'prefill_code': request.GET.get('code', ''),
            })

        my_team = Team.objects.filter(manager=request.user, league=league).first()
        rankings = scoring.compute_league_rankings(league)
        recent_deaths = (
            Death.objects.filter(
                is_confirmed=True,
                death_date__gte=league.start_date,
                death_date__lte=league.end_date,
            )
            .select_related('person')
            .order_by('-death_date')[:10]
        )
        return render(request, self.template_name, {
            'league': league,
            'my_team': my_team,
            'rankings': rankings,
            'top_rankings': rankings[:3],
            'recent_deaths': recent_deaths,
            'is_member': league.is_member(request.user),
            'is_admin': league.is_admin(request.user),
            'is_owner': league.is_owner(request.user),
            'memberships': league.memberships.select_related('user'),
        })


class LeagueJoinView(LoginRequiredMixin, View):
    def get(self, request, slug):
        """Link invito condivisibile: /leghe/<slug>/iscriviti/?code=XXX."""
        league = get_object_or_404(League, slug=slug)
        url = reverse('league_detail', kwargs={'slug': slug})
        code = request.GET.get('code', '')
        if code and not league.is_member(request.user):
            url = f'{url}?code={code}'
        return redirect(url)

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if league.is_member(request.user):
            messages.info(request, 'Sei già iscritto a questa lega.')
            return redirect('league_detail', slug=slug)
        if league.visibility == League.VISIBILITY_PRIVATE:
            code = request.POST.get('invite_code', '').strip()
            if code != league.invite_code:
                messages.error(request, 'Codice invito non valido.')
                return redirect('league_detail', slug=slug)
        LeagueMembership.objects.create(
            league=league, user=request.user, role=LeagueMembership.ROLE_MEMBER,
        )
        messages.success(request, f'Iscritto a "{league.name}". Ora crea la tua squadra!')
        return redirect('league_detail', slug=slug)


class LeagueLeaveView(LoginRequiredMixin, View):
    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if league.is_owner(request.user):
            messages.error(request, 'Il proprietario non può lasciare la lega. Trasferisci prima la proprietà.')
            return redirect('league_detail', slug=slug)
        LeagueMembership.objects.filter(league=league, user=request.user).delete()
        Team.objects.filter(league=league, manager=request.user).delete()
        messages.success(request, f'Hai lasciato la lega "{league.name}".')
        return redirect('home')


class LeagueDeleteView(LoginRequiredMixin, View):
    """Eliminazione definitiva di una lega (danger zone).

    Consentita all'owner e allo staff di sistema (che può amministrare
    qualsiasi lega, anche fuori dal Django admin). Richiede la conferma
    del nome della lega digitato per esteso.
    """

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_owner(request.user) and not request.user.is_staff:
            return HttpResponseForbidden('Solo il proprietario può eliminare la lega.')
        confirm = request.POST.get('confirm_name', '').strip()
        if confirm != league.name.strip():
            messages.error(
                request,
                'Il nome digitato non corrisponde: la lega non è stata eliminata.',
            )
            return redirect('league_admin', slug=slug)
        # I DeathBonus dei bonus personalizzati (PROTECT) contano solo in
        # questa lega: vanno rimossi prima, poi la delete cascada su squadre,
        # iscrizioni, LeagueBonus e BonusType personalizzati.
        for bt in league.custom_bonus_types.all():
            bt.awarded.all().delete()
        name = league.name
        league.delete()
        messages.success(request, f'La lega "{name}" è stata eliminata definitivamente.')
        return redirect('home')


class LeagueAdminView(LoginRequiredMixin, View):
    """Pannello di amministrazione di una lega: regole, bonus, membri, admin."""
    template_name = 'game/league_admin.html'

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        return render(request, self.template_name, {
            'league': league,
            'memberships': league.memberships.select_related('user').order_by('role', 'user__username'),
            'league_bonuses': league.league_bonuses.select_related('bonus_type').order_by('bonus_type__ordering'),
            # Bonus proponibili: quelli di sistema + i personalizzati di QUESTA lega
            'all_bonus_types': BonusType.objects.filter(
                Q(league__isnull=True) | Q(league=league)
            ).order_by('ordering'),
            'is_owner': league.is_owner(request.user),
            # Lo staff di sistema ha gli stessi poteri dell'owner su ogni
            # lega (ruoli, proprietà, eliminazione), anche fuori dal
            # Django admin.
            'can_manage_league': league.is_owner(request.user) or request.user.is_staff,
            'wiki_langs': WIKIPEDIA_LANGS,
            'league_search_langs': set(league.search_wikipedia_langs.split(',')) if league.search_wikipedia_langs else set(),
        })

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        action = request.POST.get('action', '')

        if action == 'update_rules':
            league.name = request.POST.get('name', league.name).strip() or league.name
            league.description = request.POST.get('description', league.description)
            visibility = request.POST.get('visibility', league.visibility)
            if visibility in dict(League.VISIBILITY_CHOICES):
                league.visibility = visibility
            try:
                for field in ('start_date', 'end_date', 'registration_opens', 'registration_closes'):
                    raw = request.POST.get(field)
                    if raw:
                        setattr(league, field, date.fromisoformat(raw))
            except ValueError:
                messages.error(request, 'Formato data non valido.')
                return redirect('league_admin', slug=slug)
            if league.start_date > league.end_date:
                messages.error(request, 'La data di inizio deve precedere la fine.')
                return redirect('league_admin', slug=slug)
            if league.registration_opens > league.registration_closes:
                messages.error(request, 'L\'apertura iscrizioni deve precedere la chiusura.')
                return redirect('league_admin', slug=slug)
            if league.registration_closes > league.start_date:
                messages.error(
                    request,
                    'La chiusura delle iscrizioni non può essere successiva '
                    'all\'inizio del gioco: le squadre devono essere definitive '
                    'quando i decessi iniziano a contare.',
                )
                return redirect('league_admin', slug=slug)
            try:
                league.base_points = int(request.POST.get('base_points') or league.base_points)
                league.captain_multiplier = int(request.POST.get('captain_multiplier') or league.captain_multiplier)
                league.jolly_multiplier = int(request.POST.get('jolly_multiplier') or league.jolly_multiplier)
                league.max_captains = int(request.POST.get('max_captains') or league.max_captains)
                league.max_non_captains = int(request.POST.get('max_non_captains') or league.max_non_captains)
                league.max_total_age = int(request.POST.get('max_total_age') or 0)
                league.substitution_deadline_days = int(request.POST.get('substitution_deadline_days') or league.substitution_deadline_days)
            except (ValueError, TypeError):
                messages.error(request, 'Valori numerici non validi.')
                return redirect('league_admin', slug=slug)
            league.jolly_enabled = request.POST.get('jolly_enabled') == 'on'
            league.is_locked = request.POST.get('is_locked') == 'on'
            checked_wikis = [w for w in request.POST.getlist('search_wiki_langs') if w in _VALID_WIKIS]
            league.search_wikipedia_langs = ','.join(checked_wikis)
            league.save()
            messages.success(request, 'Regole aggiornate.')

        elif action == 'rotate_invite':
            league.invite_code = secrets.token_urlsafe(8)
            league.save(update_fields=['invite_code'])
            messages.success(request, 'Nuovo codice invito generato.')

        elif action == 'set_bonus':
            for lb in league.league_bonuses.all():
                key = f'bonus_active_{lb.pk}'
                pts_key = f'bonus_points_{lb.pk}'
                lb.is_active = request.POST.get(key) == 'on'
                pts = request.POST.get(pts_key, '').strip()
                try:
                    lb.override_points = int(pts) if pts else None
                except (ValueError, TypeError):
                    lb.override_points = None
                lb.save()
            # Eventuali nuovi bonus type (di sistema o personalizzati di questa lega)
            for bt_id in request.POST.getlist('add_bonus'):
                try:
                    bt = BonusType.objects.filter(
                        Q(league__isnull=True) | Q(league=league)
                    ).get(pk=int(bt_id))
                except (BonusType.DoesNotExist, ValueError, TypeError):
                    continue
                LeagueBonus.objects.get_or_create(league=league, bonus_type=bt, defaults={'is_active': True})
            messages.success(request, 'Bonus aggiornati.')

        elif action == 'create_custom_bonus':
            name = request.POST.get('bonus_name', '').strip()
            prop = request.POST.get('bonus_wikidata_property', '').strip().upper()
            value = request.POST.get('bonus_wikidata_value', '').strip().upper()
            description = request.POST.get('bonus_description', '').strip()
            try:
                points = int(request.POST.get('bonus_points', ''))
            except (ValueError, TypeError):
                messages.error(request, 'Punti del bonus non validi.')
                return redirect('league_admin', slug=slug)
            if not name:
                messages.error(request, 'Il nome del bonus è obbligatorio.')
                return redirect('league_admin', slug=slug)
            # Proprietà Wikidata opzionale: senza, il bonus è a rilevazione
            # manuale (lo assegnano gli admin dalla pagina decessi).
            if value and not prop:
                messages.error(request, 'Hai indicato un valore Wikidata senza la proprietà.')
                return redirect('league_admin', slug=slug)
            if prop and not re.fullmatch(r'P\d+', prop):
                messages.error(request, 'Proprietà Wikidata non valida (formato: P166).')
                return redirect('league_admin', slug=slug)
            if value and not re.fullmatch(r'Q\d+', value):
                messages.error(request, 'Valore Wikidata non valido (formato: Q7191, oppure vuoto '
                                        'per "qualsiasi valore della proprietà").')
                return redirect('league_admin', slug=slug)
            if BonusType.objects.filter(league=league, name__iexact=name).exists():
                messages.error(request, 'Esiste già un bonus personalizzato con questo nome.')
                return redirect('league_admin', slug=slug)
            bt = BonusType.objects.create(
                name=name, league=league, description=description, points=points,
                detection_method=BonusType.DETECTION_WIKIDATA if prop else BonusType.DETECTION_MANUAL,
                wikidata_property=prop, wikidata_value=value,
                is_active=True, ordering=100,
            )
            LeagueBonus.objects.create(league=league, bonus_type=bt, is_active=True)
            if prop:
                messages.success(
                    request,
                    f'Bonus "{name}" creato ({prop}{"=" + value if value else ""}). Verrà rilevato '
                    'automaticamente sui prossimi decessi; per quelli già registrati usare '
                    '"Auto-rileva bonus" dal Django admin.',
                )
            else:
                messages.success(
                    request,
                    f'Bonus "{name}" creato (assegnazione manuale). Puoi assegnarlo ai decessi '
                    'dalla pagina "Decessi" della lega.',
                )

        elif action == 'delete_custom_bonus':
            try:
                bt = BonusType.objects.get(pk=int(request.POST.get('bonus_type_id', '')), league=league)
            except (BonusType.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Bonus personalizzato non trovato.')
                return redirect('league_admin', slug=slug)
            # Le righe DeathBonus di un bonus custom contano solo in questa
            # lega: eliminarle insieme al tipo è sicuro.
            bt.awarded.all().delete()
            bt.delete()
            messages.success(request, f'Bonus "{bt.name}" eliminato.')

        elif action == 'promote_admin':
            if not league.is_owner(request.user) and not request.user.is_staff:
                messages.error(request, 'Solo il proprietario può nominare admin.')
                return redirect('league_admin', slug=slug)
            try:
                m = league.memberships.get(pk=int(request.POST.get('membership_id')))
            except (LeagueMembership.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Iscrizione non trovata.')
                return redirect('league_admin', slug=slug)
            if m.role == LeagueMembership.ROLE_OWNER:
                messages.error(request, 'Non puoi modificare il proprietario.')
            else:
                m.role = LeagueMembership.ROLE_ADMIN
                m.save()
                messages.success(request, f'{m.user.username} ora è admin.')

        elif action == 'demote_admin':
            if not league.is_owner(request.user) and not request.user.is_staff:
                messages.error(request, 'Solo il proprietario può rimuovere admin.')
                return redirect('league_admin', slug=slug)
            try:
                m = league.memberships.get(pk=int(request.POST.get('membership_id')))
            except (LeagueMembership.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Iscrizione non trovata.')
                return redirect('league_admin', slug=slug)
            if m.role == LeagueMembership.ROLE_OWNER:
                messages.error(request, 'Non puoi modificare il proprietario.')
            else:
                m.role = LeagueMembership.ROLE_MEMBER
                m.save()
                messages.success(request, f'{m.user.username} è tornato membro.')

        elif action == 'remove_member':
            try:
                m = league.memberships.get(pk=int(request.POST.get('membership_id')))
            except (LeagueMembership.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Iscrizione non trovata.')
                return redirect('league_admin', slug=slug)
            if m.role == LeagueMembership.ROLE_OWNER:
                messages.error(request, 'Non puoi rimuovere il proprietario.')
            else:
                Team.objects.filter(league=league, manager=m.user).delete()
                m.delete()
                messages.success(request, 'Membro rimosso.')

        elif action == 'transfer_ownership':
            if not league.is_owner(request.user) and not request.user.is_staff:
                messages.error(request, 'Solo il proprietario può trasferire la proprietà.')
                return redirect('league_admin', slug=slug)
            try:
                m = league.memberships.get(pk=int(request.POST.get('membership_id')))
            except (LeagueMembership.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Iscrizione non trovata.')
                return redirect('league_admin', slug=slug)
            if m.user_id == league.owner_id:
                messages.info(request, f'{m.user.username} è già il proprietario.')
                return redirect('league_admin', slug=slug)
            # Il vecchio owner si cerca dal campo League.owner, non da
            # request.user: lo staff può trasferire leghe non sue.
            old_owner_membership = league.memberships.filter(user=league.owner).first()
            if old_owner_membership:
                old_owner_membership.role = LeagueMembership.ROLE_ADMIN
                old_owner_membership.save()
            m.role = LeagueMembership.ROLE_OWNER
            m.save()
            league.owner = m.user
            league.save(update_fields=['owner'])
            messages.success(request, f'Proprietà trasferita a {m.user.username}.')

        return redirect('league_admin', slug=slug)


class LeagueRankingsView(LoginRequiredMixin, View):
    """Classifica completa di una lega (pagina dedicata)."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return redirect('league_list')
        return render(request, 'game/league_rankings.html', {
            'league': league,
            'rankings': scoring.compute_league_rankings(league),
            'is_admin': league.is_admin(request.user),
        })


class LeagueScoringView(LoginRequiredMixin, View):
    """Riepilogo del regolamento di una lega, visibile a tutti i suoi membri."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return redirect('league_list')
        bonus_rows = []
        for lb in (
            league.league_bonuses.filter(is_active=True)
            .select_related('bonus_type')
            .order_by('bonus_type__ordering', 'bonus_type__name')
        ):
            bonus_rows.append({
                'bonus_type': lb.bonus_type,
                'formula': (lb.override_formula or lb.bonus_type.points_formula or '').strip(),
                'points': lb.override_points if lb.override_points is not None else lb.bonus_type.points,
            })
        return render(request, 'game/league_scoring.html', {
            'league': league,
            'bonus_rows': bonus_rows,
            'is_admin': league.is_admin(request.user),
        })


class LeagueDeathsView(LoginRequiredMixin, View):
    """Cronologia decessi di una lega, con gestione manuale dei bonus per gli admin."""

    # Bonus assegnabili a mano dagli admin: quelli manuali, più quelli
    # automatici (Wikidata/età) nel caso la rilevazione abbia mancato il dato.
    # Esclusi gli speciali (original/first/last): li calcola lo scoring.
    ASSIGNABLE_METHODS = (
        BonusType.DETECTION_MANUAL, BonusType.DETECTION_WIKIDATA, BonusType.DETECTION_AGE,
    )

    def _deaths(self, league):
        return (
            Death.objects.filter(
                is_confirmed=True,
                death_date__gte=league.start_date,
                death_date__lte=league.end_date,
            )
            .select_related('person')
            .prefetch_related('bonuses__bonus_type')
            .order_by('-death_date')
        )

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return redirect('league_list')
        is_admin = league.is_admin(request.user)
        lb_map = {
            lb.bonus_type_id: lb
            for lb in league.league_bonuses.filter(is_active=True).select_related('bonus_type')
        }
        first_pk, last_pk = scoring.league_first_last_death_pks(league)
        first_lb = next((lb for lb in lb_map.values()
                         if lb.bonus_type.detection_method == BonusType.DETECTION_FIRST_DEATH), None)
        last_lb = next((lb for lb in lb_map.values()
                        if lb.bonus_type.detection_method == BonusType.DETECTION_LAST_DEATH), None)

        death_rows = []
        for d in self._deaths(league):
            items = []
            for b in d.bonuses.all():
                bt = b.bonus_type
                # Righe legacy primo/ultimo: ignorate dallo scoring, non mostrarle.
                if bt.detection_method in (BonusType.DETECTION_FIRST_DEATH, BonusType.DETECTION_LAST_DEATH):
                    continue
                # Bonus personalizzati di ALTRE leghe: irrilevanti qui.
                if bt.league_id and bt.league_id != league.pk:
                    continue
                lb = lb_map.get(bt.pk)
                items.append({
                    'bonus': b,
                    'active': lb is not None,
                    'points': lb.compute_points(age=d.death_age) if lb else None,
                    # I bonus assegnati a mano si possono revocare da qui; quelli
                    # auto-rilevati solo se personalizzati della lega (gli altri
                    # sono condivisi e si revocano dal Django admin).
                    'removable': is_admin and (not b.is_auto_detected or bt.league_id == league.pk),
                })
            death_rows.append({
                'death': d,
                'bonus_items': items,
                'is_first': first_lb is not None and d.pk == first_pk,
                'first_points': first_lb.compute_points(age=d.death_age) if first_lb else None,
                'is_last': last_lb is not None and last_pk is not None and d.pk == last_pk,
                'last_points': last_lb.compute_points(age=d.death_age) if last_lb else None,
            })

        assignable = sorted(
            (lb for lb in lb_map.values()
             if lb.bonus_type.detection_method in self.ASSIGNABLE_METHODS),
            key=lambda lb: (lb.bonus_type.ordering, lb.bonus_type.name),
        ) if is_admin else []
        return render(request, 'game/league_deaths.html', {
            'league': league,
            'death_rows': death_rows,
            'is_admin': is_admin,
            'assignable_bonuses': assignable,
        })

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        action = request.POST.get('action', '')

        if action == 'assign_bonus':
            try:
                death = Death.objects.select_related('person').get(
                    pk=int(request.POST.get('death_id', '')),
                    is_confirmed=True,
                    death_date__gte=league.start_date,
                    death_date__lte=league.end_date,
                )
                lb = league.league_bonuses.select_related('bonus_type').get(
                    bonus_type_id=int(request.POST.get('bonus_type_id', '')),
                    is_active=True,
                    bonus_type__detection_method__in=self.ASSIGNABLE_METHODS,
                )
            except (Death.DoesNotExist, LeagueBonus.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Decesso o bonus non valido.')
                return redirect('league_deaths', slug=slug)
            _, created = DeathBonus.objects.get_or_create(
                death=death, bonus_type=lb.bonus_type,
                defaults={'points_awarded': lb.bonus_type.points, 'is_auto_detected': False},
            )
            if created:
                messages.success(
                    request, f'Bonus "{lb.bonus_type.name}" assegnato a {death.person.name_it}.'
                )
            else:
                messages.info(request, 'Bonus già assegnato a questo decesso.')

        elif action == 'remove_bonus':
            try:
                db = DeathBonus.objects.select_related('death__person', 'bonus_type').get(
                    pk=int(request.POST.get('death_bonus_id', '')),
                    death__death_date__gte=league.start_date,
                    death__death_date__lte=league.end_date,
                )
            except (DeathBonus.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Bonus non trovato.')
                return redirect('league_deaths', slug=slug)
            if db.bonus_type.league_id and db.bonus_type.league_id != league.pk:
                messages.error(request, 'Questo bonus appartiene a un\'altra lega.')
            elif db.is_auto_detected and db.bonus_type.league_id != league.pk:
                messages.error(
                    request,
                    'I bonus di sistema rilevati automaticamente si revocano dal Django admin.',
                )
            else:
                db.delete()
                messages.success(
                    request,
                    f'Bonus "{db.bonus_type.name}" rimosso da {db.death.person.name_it}.',
                )

        return redirect('league_deaths', slug=slug)


# ---------------- Squadre ----------------

class TeamDetailView(LoginRequiredMixin, DetailView):
    model = Team
    template_name = 'game/team_detail.html'
    context_object_name = 'team'

    def get_object(self, queryset=None):
        team = super().get_object(queryset)
        if team.league_id and not team.league.can_user_view(self.request.user):
            raise Http404('Squadra di una lega privata.')
        return team

    def get_queryset(self):
        # I membri prefetchati vengono riusati da _find_member nello scoring.
        return Team.objects.select_related('league', 'manager').prefetch_related('members__person')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        team = self.object
        details = scoring.compute_team_death_details(team)
        ctx['death_details'] = details
        ctx['score'] = sum(d['points'] for d in details)
        ctx['active_members'] = [m for m in team.members.all() if m.is_active()]
        return ctx


class DeathDetailView(LoginRequiredMixin, DetailView):
    model = Death
    template_name = 'game/death_detail.html'
    context_object_name = 'death'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        death = self.object
        ctx['bonuses'] = death.bonuses.select_related('bonus_type')
        ctx['teams_affected'] = []
        members = TeamMember.objects.filter(person=death.person).select_related(
            'team__manager', 'team__league',
        )
        for member in members:
            league = member.team.league
            # Le squadre di leghe private restano visibili solo ai membri.
            if league and not league.can_user_view(self.request.user):
                continue
            pts = scoring.compute_team_points_for_death(member.team, death)
            if pts:
                ctx['teams_affected'].append({'team': member.team, 'points': pts})
        return ctx


WIKIPEDIA_LANGS = [
    ('itwiki', 'Italiano (it)'),
    ('enwiki', 'English (en)'),
    ('frwiki', 'Français (fr)'),
    ('dewiki', 'Deutsch (de)'),
    ('eswiki', 'Español (es)'),
    ('ptwiki', 'Português (pt)'),
    ('ruwiki', 'Русский (ru)'),
    ('zhwiki', '中文 (zh)'),
    ('jawiki', '日本語 (ja)'),
    ('arwiki', 'العربية (ar)'),
    ('nlwiki', 'Nederlands (nl)'),
    ('plwiki', 'Polski (pl)'),
]
_VALID_WIKIS = {code for code, _ in WIKIPEDIA_LANGS}

def _team_edit_window_open(team):
    """Regole di gioco: la rosa è modificabile solo con le registrazioni
    aperte e senza lock di lega o squadra."""
    if team.is_locked:
        return False
    if team.league_id:
        return team.league.is_registration_open() and not team.league.is_locked
    return False


def _can_edit_team(team, user):
    """Editing della rosa: aperto al solo manager finché la finestra di
    modifica è aperta (vedi _team_edit_window_open). Nessun override per lo
    staff: la UI di gioco è identica per tutti, gli interventi eccezionali
    sulle rose si fanno dal Django admin. Le sostituzioni in stagione NON
    passano da qui: sono governate da can_be_substituted()."""
    if team.manager_id != user.pk:
        return False
    return _team_edit_window_open(team)


def _get_or_refresh_person(wikidata_id):
    """Ritorna ``(person, error_message)`` per un QID Wikidata.

    Se la persona è in cache locale ed è stata verificata entro
    ``wikidata_check_interval_hours`` non tocca la rete; altrimenti fa il
    fetch da Wikidata e sincronizza (o crea) il record con lo stesso core
    del cron ``check_deaths`` e della pagina admin giocatori.
    """
    interval = SiteSettings.get().wikidata_check_interval_hours
    threshold = timezone.now() - timedelta(hours=interval)
    existing = WikipediaPerson.objects.filter(wikidata_id=wikidata_id).first()
    if existing and existing.last_checked and existing.last_checked >= threshold:
        return existing, None
    client = WikidataClient()
    try:
        entity = client.get_entity(wikidata_id)
    except Exception as e:
        return existing, f'Errore Wikidata: {e}'
    person = existing or WikipediaPerson(wikidata_id=wikidata_id)
    person_sync.sync_person_from_entity(person, entity, client=client)
    return person, None


class TeamCreateView(LoginRequiredMixin, View):
    """Crea (se non esiste) la squadra dell'utente in una lega e porta
    direttamente alla pagina di modifica, dove nome, mese jolly e rosa si
    gestiscono in un unico form."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_member(request.user):
            messages.error(request, 'Devi prima iscriverti alla lega.')
            return redirect('league_detail', slug=slug)
        if not league.is_registration_open():
            messages.error(request, 'Le registrazioni non sono aperte per questa lega.')
            return redirect('league_detail', slug=slug)
        team, _ = Team.objects.get_or_create(
            manager=request.user, league=league,
            defaults={'name': f'Squadra di {request.user.username}'},
        )
        return redirect('team_edit', pk=team.pk)


class TeamDeleteView(LoginRequiredMixin, View):
    """Eliminazione definitiva di una squadra (danger zone).

    Richiede la conferma del nome della squadra digitato per esteso,
    come per l'eliminazione della lega.
    """

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            messages.error(request, 'Non hai i permessi per eliminare questa squadra.')
            return redirect('team_detail', pk=pk)
        if not _can_edit_team(team, request.user):
            messages.error(request, 'Non è più possibile eliminare la squadra.')
            return redirect('team_edit', pk=pk)
        confirm = request.POST.get('confirm_name', '').strip()
        if confirm != team.name.strip():
            messages.error(
                request,
                'Il nome digitato non corrisponde: la squadra non è stata eliminata.',
            )
            return redirect('team_edit', pk=pk)
        league = team.league
        name = team.name
        team.delete()
        messages.success(request, f'Squadra "{name}" eliminata.')
        if league:
            return redirect('league_detail', slug=league.slug)
        return redirect('home')


class TeamEditView(LoginRequiredMixin, View):
    template_name = 'game/team_edit.html'

    def get(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            messages.error(request, 'Non hai i permessi per modificare questa squadra.')
            return redirect('team_detail', pk=pk)
        league = team.league
        members = team.members.select_related('person').order_by('-is_captain', 'person__name_it')
        dead_members = [m for m in members if m.person.is_dead and m.is_active()]
        active_count = sum(1 for m in members if m.is_active())
        can_edit = _can_edit_team(team, request.user)
        edit_window_open = _team_edit_window_open(team)
        return render(request, self.template_name, {
            'team': team,
            'league': league,
            'members': members,
            'active_count': active_count,
            'total_age': team.get_active_total_age(),
            'dead_members': dead_members,
            'months': MONTHS_IT,
            'can_edit': can_edit,
            'edit_window_open': edit_window_open,
            'max_non_captains': league.max_non_captains if league else 11,
            'max_captains': league.max_captains if league else 1,
        })

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            return redirect('team_detail', pk=pk)
        if not _can_edit_team(team, request.user):
            messages.error(request, 'Non è più possibile modificare la squadra.')
            return redirect('team_edit', pk=pk)

        name = request.POST.get('name', '').strip()
        jolly_month = request.POST.get('jolly_month', '')
        captain_id = request.POST.get('captain_id', '')

        if name:
            team.name = name
        if jolly_month and (not team.league or team.league.jolly_enabled):
            try:
                jolly_month = int(jolly_month)
            except (TypeError, ValueError):
                jolly_month = None
            if jolly_month is not None and 1 <= jolly_month <= 12:
                team.jolly_month = jolly_month
            else:
                messages.error(request, 'Mese jolly non valido.')
        team.save()

        if captain_id:
            try:
                captain_pk = int(captain_id)
            except (TypeError, ValueError):
                captain_pk = None
            if captain_pk is not None:
                team.members.update(is_captain=False)
                team.members.filter(pk=captain_pk).update(is_captain=True)

        messages.success(request, 'Squadra aggiornata.')
        return redirect('team_edit', pk=pk)


class AddPersonView(LoginRequiredMixin, View):
    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            return JsonResponse({'error': 'Permesso negato'}, status=403)
        if not _can_edit_team(team, request.user):
            return JsonResponse({'error': 'Registrazioni chiuse'}, status=400)
        league = team.league
        max_captains = league.max_captains if league else 1
        max_non_captains = league.max_non_captains if league else 11

        wikidata_id = request.POST.get('wikidata_id', '').strip()
        is_captain = request.POST.get('is_captain') == '1'

        if not wikidata_id:
            return JsonResponse({'error': 'wikidata_id mancante'}, status=400)
        if not re.fullmatch(r'Q\d+', wikidata_id):
            return JsonResponse({'error': 'wikidata_id non valido'}, status=400)

        person, err = _get_or_refresh_person(wikidata_id)
        if err:
            return JsonResponse({'error': err}, status=500)

        if person.is_dead:
            return JsonResponse({'error': f'{person.name_it} è già morto/a e non può essere aggiunto.'}, status=400)

        # Check if already on this team as active member
        if team.members.filter(person=person, replaced_by=None).exists():
            return JsonResponse({'error': f'{person.name_it} è già nella squadra.'}, status=400)

        # Check team size limits
        active_non_captain = team.get_active_non_captain_count()
        active_captain = team.members.filter(is_captain=True, replaced_by=None).count()

        if is_captain:
            if active_captain >= max_captains:
                return JsonResponse({'error': f'La squadra ha già {max_captains} capitano/i.'}, status=400)
        else:
            if active_non_captain >= max_non_captains:
                return JsonResponse({'error': f'La squadra ha già {max_non_captains} morituri.'}, status=400)

        if league and league.max_total_age:
            new_age = person.get_current_age() or 0
            total_age = team.get_active_total_age()
            if total_age + new_age > league.max_total_age:
                return JsonResponse({'error': (
                    f'Limite età superato: la rosa somma {total_age} anni e con '
                    f'{person.name_it} ({new_age}) arriverebbe a {total_age + new_age} '
                    f'su un massimo di {league.max_total_age}.'
                )}, status=400)

        member = TeamMember.objects.create(team=team, person=person, is_captain=is_captain)
        return JsonResponse({
            'success': True,
            'member_id': member.pk,
            'name': person.name_it,
            'wikidata_id': person.wikidata_id,
            'is_captain': is_captain,
        })


class RemovePersonView(LoginRequiredMixin, View):
    """Rimuove un membro dalla rosa finché la squadra è modificabile.

    Consentito solo in fase di composizione (stesse regole di AddPersonView):
    membro attivo, persona ancora viva e non subentrata a una sostituzione
    (rimuoverla riattiverebbe il membro sostituito).
    """

    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            return JsonResponse({'error': 'Permesso negato'}, status=403)
        if not _can_edit_team(team, request.user):
            return JsonResponse({'error': 'La squadra non è più modificabile.'}, status=400)
        member = get_object_or_404(TeamMember, pk=member_pk, team=team)
        if not member.is_active():
            return JsonResponse({'error': 'Membro già sostituito.'}, status=400)
        if member.person.is_dead:
            return JsonResponse(
                {'error': f'{member.person.name_it} è deceduto/a: usa la sostituzione.'},
                status=400)
        if TeamMember.objects.filter(replaced_by=member).exists():
            return JsonResponse(
                {'error': 'Questo membro è subentrato con una sostituzione e non può essere rimosso.'},
                status=400)
        name = member.person.name_it
        member.delete()
        return JsonResponse({'success': True, 'name': name})


class SubstituteMemberView(LoginRequiredMixin, View):
    template_name = 'game/substitute_member.html'

    def get(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        member = get_object_or_404(TeamMember, pk=member_pk, team=team)
        if team.manager != request.user and not request.user.is_staff:
            messages.error(request, 'Permesso negato.')
            return redirect('team_edit', pk=pk)
        if not member.person.is_dead:
            messages.error(request, 'Questo membro non è ancora morto.')
            return redirect('team_edit', pk=pk)
        if not member.is_active():
            messages.error(request, 'Questo membro è già stato sostituito.')
            return redirect('team_edit', pk=pk)
        if not member.can_be_substituted():
            days = team.league.substitution_deadline_days if team.league_id else 7
            messages.error(
                request,
                f'I tempi per la sostituzione sono scaduti ({days} giorni).'
            )
            return redirect('team_edit', pk=pk)
        return render(request, self.template_name, {
            'team': team,
            'member': member,
            'deadline': member.get_substitution_deadline(),
            'seconds_left': member.substitution_seconds_remaining(),
        })

    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        member = get_object_or_404(TeamMember, pk=member_pk, team=team)
        if team.manager != request.user and not request.user.is_staff:
            return redirect('team_edit', pk=pk)
        if not member.can_be_substituted():
            messages.error(request, 'I tempi per la sostituzione sono scaduti.')
            return redirect('team_edit', pk=pk)

        wikidata_id = request.POST.get('wikidata_id', '').strip()
        if not wikidata_id:
            messages.error(request, 'Seleziona una persona da Wikidata.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)
        if not re.fullmatch(r'Q\d+', wikidata_id):
            messages.error(request, 'Identificativo Wikidata non valido.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        person, err = _get_or_refresh_person(wikidata_id)
        if err:
            messages.error(request, err)
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        if person.is_dead:
            messages.error(request, f'{person.name_it} è già morto/a.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        if team.members.filter(person=person, replaced_by=None).exists():
            messages.error(request, f'{person.name_it} è già nella squadra.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        league = team.league
        if league and league.max_total_age:
            new_age = person.get_current_age() or 0
            old_age = member.person.get_current_age() or 0
            projected = team.get_active_total_age() - old_age + new_age
            if projected > league.max_total_age:
                messages.error(
                    request,
                    f'Limite età superato: con {person.name_it} ({new_age} anni) la rosa '
                    f'arriverebbe a {projected} anni su un massimo di {league.max_total_age}.',
                )
                return redirect('substitute_member', pk=pk, member_pk=member_pk)

        new_member = TeamMember.objects.create(
            team=team, person=person, is_captain=member.is_captain
        )
        member.replaced_by = new_member
        member.save()

        messages.success(request, f'{member.person.name_it} sostituito/a con {person.name_it}.')
        return redirect('team_edit', pk=pk)


class PersonDetailView(LoginRequiredMixin, DetailView):
    """Pagina di dettaglio di una persona della rosa."""
    model = WikipediaPerson
    template_name = 'game/person_detail.html'
    context_object_name = 'person'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        person = self.object
        # _refresh_person_summary è già no-op se il summary è fresco: la
        # guardia su summary_it lo lascerebbe stale per sempre oltre i 30 gg.
        if person.wikipedia_url_it:
            _refresh_person_summary(person)
        members = TeamMember.objects.filter(person=person).select_related(
            'team__manager', 'team__league',
        )
        # Le squadre di leghe private restano visibili solo ai membri.
        ctx['team_members'] = [
            m for m in members
            if not m.team.league_id or m.team.league.can_user_view(self.request.user)
        ]
        return ctx


def _summary_is_stale(person):
    """True se il summary Wikipedia manca o è più vecchio di 30 giorni."""
    if not person.wikipedia_url_it:
        return False
    if person.summary_it and person.summary_fetched_at:
        return (timezone.now() - person.summary_fetched_at) > timedelta(days=30)
    return True


def _refresh_person_summary(person):
    """Aggiorna `summary_it` da Wikipedia se mancante o piu' vecchio di 30 giorni."""
    if not _summary_is_stale(person):
        return
    try:
        title = unquote(person.wikipedia_url_it.rsplit('/', 1)[-1].replace('_', ' '))
        summary = WikidataClient().get_summary(title)
    except Exception:
        logger.warning('Refresh summary fallito per %s', person.wikidata_id, exc_info=True)
        return
    if summary:
        person.summary_it = summary
        person.summary_fetched_at = timezone.now()
        person.save(update_fields=['summary_it', 'summary_fetched_at'])


def _potential_league_bonuses(person, league):
    """Bonus automatici (Wikidata/età) che scatterebbero per questa persona
    se il decesso avvenisse oggi, con i punti effettivi della lega.

    Solo per persone vive: per i decessi reali fanno fede i DeathBonus.
    Il match Wikidata usa i claim in cache (+ eventuale check gerarchico,
    a sua volta cachato 7 giorni dal client); il risultato complessivo è
    cachato 1 ora per non rallentare l'apertura del modal.
    """
    if person.is_dead:
        return []
    # La chiave include la versione di cache della lega: cambi a regole o
    # bonus (pannello admin) la fanno scadere subito, senza attendere il TTL.
    cache_key = f'fm_potential:{league.pk}:{scoring.league_cache_version(league.pk)}:{person.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    age = person.get_current_age()
    league_bonuses = list(
        league.league_bonuses.filter(is_active=True).select_related('bonus_type')
    )
    client = WikidataClient()
    client.delay = 0          # richiesta interattiva (modal aperto)
    client.sparql_timeout = 8  # fail-fast sui check gerarchici non in cache
    wikidata_types = [
        lb.bonus_type for lb in league_bonuses
        if lb.bonus_type.detection_method == BonusType.DETECTION_WIKIDATA
    ]
    try:
        detected = client.detect_bonuses(
            person.wikidata_id, person.claims_cache or {}, wikidata_types)
    except Exception:
        logger.warning('Detection bonus potenziali fallita per %s',
                       person.wikidata_id, exc_info=True)
        detected = []
    detected_pks = {bt.pk for bt in detected}
    rows = []
    for lb in league_bonuses:
        bt = lb.bonus_type
        if bt.detection_method == BonusType.DETECTION_WIKIDATA:
            hit = bt.pk in detected_pks
        elif bt.detection_method == BonusType.DETECTION_AGE:
            hit = age is not None and client.detect_age_bonus(age, bt)
        else:
            continue
        if hit:
            rows.append({'name': bt.name, 'points': lb.compute_points(age=age)})
    cache.set(cache_key, rows, 3600)
    return rows


class PersonInfoView(LoginRequiredMixin, View):
    """Endpoint JSON per il pannello dettagli persona (open su click).

    Con ``?league=<slug>`` include anche i bonus automatici della lega che
    scatterebbero se il decesso avvenisse oggi (solo per persone vive).
    """

    def get(self, request, pk):
        person = get_object_or_404(WikipediaPerson, pk=pk)
        # Nessuna chiamata a Wikipedia qui: il modal deve aprirsi subito.
        # Se il summary manca o è scaduto il client lo carica in un secondo
        # momento da PersonSummaryView (campo summary_stale).
        data = {
            'id': person.pk,
            'wikidata_id': person.wikidata_id,
            'name_it': person.name_it,
            'description_it': person.description_it,
            'birth_date': person.birth_date.isoformat() if person.birth_date else (str(person.birth_year) if person.birth_year else ''),
            'death_date': person.death_date.isoformat() if person.death_date else '',
            'is_dead': person.is_dead,
            'age_at_death': person.get_age_at_death(),
            'occupation': person.occupation,
            'nationality': person.nationality,
            'image_url': person.image_url,
            'wikipedia_url_it': person.wikipedia_url_it,
            'summary_it': person.summary_it,
            'summary_stale': _summary_is_stale(person),
            'wikidata_url': f'https://www.wikidata.org/wiki/{person.wikidata_id}',
        }
        league_slug = request.GET.get('league', '').strip()
        if league_slug and not person.is_dead:
            league = League.objects.filter(slug=league_slug).first()
            if league and league.can_user_view(request.user):
                data['league_name'] = league.name
                data['base_points'] = league.base_points
                data['potential_bonuses'] = _potential_league_bonuses(person, league)
        return JsonResponse(data)


class PersonSummaryView(LoginRequiredMixin, View):
    """Refresh sincrono del summary Wikipedia, chiamato lazy dal modal.

    Separato da PersonInfoView così il modal apre subito con i dati in DB
    e la (eventuale) attesa di Wikipedia riguarda solo la biografia.
    """

    def get(self, request, pk):
        person = get_object_or_404(WikipediaPerson, pk=pk)
        _refresh_person_summary(person)  # no-op se fresco
        return JsonResponse({
            'summary_it': person.summary_it,
            # Ancora stale dopo il refresh = fetch fallito: il client
            # mantiene quello che sta già mostrando.
            'summary_stale': _summary_is_stale(person),
        })


class PersonSearchView(LoginRequiredMixin, View):
    def get(self, request):
        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'results': []})
        league_slug = request.GET.get('league', '')
        require_wikis = None
        if league_slug:
            league_obj = League.objects.filter(slug=league_slug).first()
            if league_obj and league_obj.search_wikipedia_langs:
                require_wikis = [w for w in league_obj.search_wikipedia_langs.split(',') if w]

        raw_key = f'wds:{q.lower()}:{",".join(require_wikis) if require_wikis else ""}'
        cache_key = 'wds:' + hashlib.md5(raw_key.encode()).hexdigest()
        results = cache.get(cache_key)
        if results is not None:
            return JsonResponse({'results': results})

        client = WikidataClient()
        client.delay = 0  # ricerca interattiva: nessun rate-limit artificiale
        # Fail-fast: meglio un fallback rapido che un autocomplete appeso.
        client.timeout = 5
        client.sparql_timeout = 8
        sparql_warning = None
        try:
            results, sparql_failed = client.search_by_italian_name(q, require_wikis=require_wikis)
            if sparql_failed:
                sparql_warning = 'Wikidata lento: risultati non filtrati per lingua. Verifica la persona prima di aggiungerla.'
            else:
                cache.set(cache_key, results, 300)  # 5 minuti
        except Exception:
            logger.exception('Wikidata search failed for q=%r', q)
            results = []
        response = {'results': results}
        if sparql_warning:
            response['warning'] = sparql_warning
        return JsonResponse(response)


class RulesView(TemplateView):
    """Manuale generico del portale: nessun punteggio, quelli sono per-lega."""
    template_name = 'game/rules.html'


class ProfileView(LoginRequiredMixin, View):
    template_name = 'game/profile.html'

    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        subs = request.user.push_subscriptions.all()
        teams = request.user.teams.select_related('league').order_by('-league__start_date')
        return render(request, self.template_name, {
            'profile': profile,
            'push_subscriptions': subs,
            'teams': teams,
        })

    def post(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.push_notifications_enabled = request.POST.get('push_notifications_enabled') == 'on'
        profile.email_notifications_enabled = request.POST.get('email_notifications_enabled') == 'on'
        theme = request.POST.get('theme_preference')
        valid_themes = {choice[0] for choice in UserProfile.THEME_CHOICES}
        if theme in valid_themes:
            profile.theme_preference = theme
        profile.save()
        messages.success(request, 'Preferenze aggiornate.')
        return redirect('profile')


# --- PWA: manifest, service worker, offline ---

class ManifestView(View):
    @method_decorator(cache_control(max_age=3600))
    def get(self, request):
        manifest = {
            'name': settings.PWA_APP_NAME,
            'short_name': settings.PWA_APP_SHORT_NAME,
            'description': 'Il fantacalcio dei decessi: sfida i tuoi amici a pronosticare chi se ne andrà.',
            'start_url': '/',
            'scope': '/',
            'display': 'standalone',
            'orientation': 'portrait-primary',
            'background_color': settings.PWA_APP_BACKGROUND_COLOR,
            'theme_color': settings.PWA_APP_THEME_COLOR,
            'lang': 'it-IT',
            # URL risolti da static(): in produzione (ManifestStaticFilesStorage)
            # sono i nomi con hash, così gli aggiornamenti delle icone non
            # restano bloccati dalla cache lunga di nginx sui path non hashati.
            'icons': [
                {'src': static('pwa/icon-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                {'src': static('pwa/icon-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                {'src': static('pwa/icon.svg'), 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'},
                # maskable: plate a tutto sangue + glifo in safe-zone, per le
                # icone adattive Android (senza, l'icona viene letterboxed).
                {'src': static('pwa/icon-maskable-192.png'), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'maskable'},
                {'src': static('pwa/icon-maskable-512.png'), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
                # monochrome: silhouette trasparente, abilita le themed icons.
                {'src': static('pwa/badge-96.png'), 'sizes': '96x96', 'type': 'image/png', 'purpose': 'monochrome'},
            ],
            # Solo URL esistenti: le pagine classifica/decessi sono per-lega.
            'shortcuts': [
                {'name': 'Le mie leghe', 'url': '/'},
                {'name': 'Profilo', 'url': '/profilo/'},
            ],
            'categories': ['games', 'entertainment'],
        }
        return JsonResponse(manifest)


class ServiceWorkerView(View):
    @method_decorator(cache_control(max_age=0, no_cache=True, no_store=True, must_revalidate=True))
    def get(self, request):
        return render(
            request,
            'game/sw.js',
            content_type='application/javascript',
            context={
                'cache_version': getattr(settings, 'SW_CACHE_VERSION', '1'),
            },
        )


class OfflineView(TemplateView):
    template_name = 'game/offline.html'


class HealthCheckView(View):
    """Endpoint di healthcheck per compose/monitoring: verifica anche il DB."""

    def get(self, request):
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        except Exception:
            logger.exception('Healthcheck DB fallito')
            return JsonResponse({'status': 'error', 'db': 'unreachable'}, status=503)
        return JsonResponse({'status': 'ok'})


# --- Push subscriptions API ---

class PushSubscribeView(LoginRequiredMixin, View):
    """Salva l'endpoint Web Push del browser corrente."""

    def post(self, request):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'JSON non valido'}, status=400)

        endpoint = data.get('endpoint', '').strip()
        keys = data.get('keys') or {}
        p256dh = (keys.get('p256dh') or '').strip()
        auth = (keys.get('auth') or '').strip()
        if not endpoint or not p256dh or not auth:
            return JsonResponse({'error': 'Sottoscrizione incompleta'}, status=400)

        sub, created = PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh': p256dh,
                'auth': auth,
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
            },
        )
        return JsonResponse({'success': True, 'created': created, 'id': sub.pk})


class PushUnsubscribeView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'JSON non valido'}, status=400)
        endpoint = data.get('endpoint', '').strip()
        deleted, _ = PushSubscription.objects.filter(
            user=request.user, endpoint=endpoint
        ).delete()
        return JsonResponse({'success': True, 'deleted': deleted})


class PushTestView(LoginRequiredMixin, View):
    """Invia una notifica di test all'utente corrente."""

    def post(self, request):
        from .push import send_push
        subs = request.user.push_subscriptions.all()
        if not subs.exists():
            return JsonResponse({'error': 'Nessuna iscrizione attiva'}, status=400)
        sent = 0
        for sub in subs:
            ok = send_push(sub, {
                'type': 'test',
                'title': '☠ Fantamorte — test',
                'body': 'Le notifiche funzionano correttamente.',
                'url': reverse('home'),
                'tag': 'test',
            })
            if ok:
                sent += 1
        return JsonResponse({'success': True, 'sent': sent, 'total': subs.count()})


# ---------------------------------------------------------------------------
# Giocatori Wikidata – diff e apply (pannello admin lega)
# ---------------------------------------------------------------------------

DIFF_FIELDS = [
    ('name_it', 'Nome italiano'),
    ('name_en', 'Nome inglese'),
    ('description_it', 'Descrizione'),
    ('birth_date', 'Data di nascita'),
    ('birth_year', 'Anno di nascita'),
    ('death_date', 'Data di morte'),
    ('death_year', 'Anno di morte'),
    ('image_url', 'Immagine'),
    ('occupation', 'Professione'),
    ('nationality', 'Nazionalità'),
]

def _league_persons(league):
    """Tutti i WikipediaPerson distinti in team attivi (non sostituiti) della lega."""
    return WikipediaPerson.objects.filter(
        team_members__team__league=league,
        team_members__replaced_by__isnull=True,
    ).distinct().order_by('name_it')


def _compute_diff(person, entity):
    """Restituisce lista di dizionari {field, label, old, new, is_removal}."""
    changes = []
    for field, label in DIFF_FIELDS:
        old_val = getattr(person, field)
        new_val = entity.get(field)
        # None significa "dato non determinabile da Wikidata" (es. timeout label lookup):
        # non mostrare come diff per evitare falsi positivi.
        if new_val is None and old_val is not None and old_val != '':
            continue
        if field == 'death_date' or field == 'birth_date':
            if old_val is not None:
                old_val = old_val.isoformat()
            if new_val is not None and hasattr(new_val, 'isoformat'):
                new_val = new_val.isoformat()
        if old_val != new_val:
            changes.append({
                'field': field,
                'label': label,
                'old': str(old_val) if old_val is not None else None,
                'new': str(new_val) if new_val is not None else None,
                'is_removal': old_val is not None and new_val is None,
            })
    return changes


class LeaguePlayersRefreshView(LoginRequiredMixin, View):
    template_name = 'game/league_players_refresh.html'

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        persons = _league_persons(league)
        return render(request, self.template_name, {
            'league': league,
            'persons': persons,
        })


# Numero massimo di persone per singola richiesta di sync: il fetch da
# Wikidata è sequenziale, un batch illimitato sfora il timeout Gunicorn
# (60s). Il client spezza il "Controlla tutti" in blocchi di questa taglia.
MAX_DIFF_BATCH = 10


class LeagueBulkDiffView(LoginRequiredMixin, View):
    """POST JSON → sincronizza da Wikidata un blocco di persone della lega.

    È l'equivalente manuale del cron ``check_deaths``: stesso core
    (:func:`game.person_sync.sync_person_from_entity`), quindi stessa
    applicazione di campi, claims, cache e registrazione dei decessi con
    punti e notifiche. La risposta riporta le differenze applicate.
    Le persone ``data_frozen`` vengono saltate, come nei check automatici.
    """

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return JsonResponse({'error': 'Permesso negato'}, status=403)

        try:
            body = json.loads(request.body or '{}')
        except (ValueError, UnicodeDecodeError):
            return JsonResponse({'error': 'JSON non valido'}, status=400)

        person_pks = body.get('person_pks') or []
        if not isinstance(person_pks, list) or not person_pks:
            return JsonResponse(
                {'error': f'person_pks obbligatorio (max {MAX_DIFF_BATCH} per richiesta)'},
                status=400)
        if len(person_pks) > MAX_DIFF_BATCH:
            return JsonResponse(
                {'error': f'Troppe persone in una richiesta (max {MAX_DIFF_BATCH})'},
                status=400)
        persons = WikipediaPerson.objects.filter(
            pk__in=person_pks,
            team_members__team__league=league,
            team_members__replaced_by__isnull=True,
        ).distinct()

        client = WikidataClient()
        results = []
        for person in persons:
            # data_frozen = "i dati Wikidata di questa persona sono
            # errati/incompleti, non applicarli": vale anche per la
            # sincronizzazione manuale, come per il cron.
            if person.data_frozen:
                results.append({
                    'person_pk': person.pk,
                    'wikidata_id': person.wikidata_id,
                    'name_it': person.name_it,
                    'frozen': True,
                    'changes': [],
                })
                continue
            try:
                entity = client.get_entity(person.wikidata_id)
            except Exception as e:
                results.append({
                    'person_pk': person.pk,
                    'wikidata_id': person.wikidata_id,
                    'name_it': person.name_it,
                    'error': str(e),
                    'changes': [],
                })
                continue
            # Report delle differenze calcolato PRIMA della sync (per il
            # rendering); l'applicazione vera è il core condiviso, identico
            # al cron: campi, claims, cache, registrazione decesso.
            changes = _compute_diff(person, entity)
            death, death_created = person_sync.sync_person_from_entity(
                person, entity, client=client,
            )
            results.append({
                'person_pk': person.pk,
                'wikidata_id': person.wikidata_id,
                'name_it': person.name_it,
                'changes': changes,
                'is_dead': person.is_dead,
                'death_registered': death_created,
                'last_checked': (
                    timezone.localtime(person.last_checked).strftime('%d/%m/%Y %H:%M')
                    if person.last_checked else None
                ),
            })
        return JsonResponse({'results': results})


# ---------------- Simulatore What-If ----------------

class TeamWhatIfView(LoginRequiredMixin, View):
    """Simulatore: per una squadra dell'utente, mostra i punti che farebbe
    ogni membro vivo se morisse oggi (bonus automatici della lega inclusi).
    Aiuta a scegliere capitano/jolly."""

    template_name = 'game/team_what_if.html'

    def get(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager_id != request.user.id and not request.user.is_staff:
            return HttpResponseForbidden('Non sei il proprietario di questa squadra.')

        try:
            month = int(request.GET.get('month') or timezone.now().month)
        except (TypeError, ValueError):
            month = timezone.now().month
        month = max(1, min(12, month))

        league = team.league if team.league_id else None
        active_members = team.members.filter(replaced_by__isnull=True).select_related('person')
        rows = []
        for m in active_members:
            person = m.person
            # Età attuale (o al decesso, per i già morti); 80 se mancano i dati.
            age = person.get_current_age()
            if age is None:
                age = 80
            # Bonus automatici (Wikidata/età, anche personalizzati di lega):
            # stessa detection e stessa cache del modal "se morisse oggi".
            bonuses = _potential_league_bonuses(person, league) if league else []
            points = scoring.simulate_team_points_for_person(
                team, person, age, death_month=month,
                extra_bonus_points=sum(b['points'] for b in bonuses),
            )
            rows.append({
                'member': m,
                'person': person,
                'simulated_age': age,
                'bonuses': bonuses,
                'points_now': points,
                'is_jolly_month': team.jolly_month == month,
            })
        rows.sort(key=lambda r: -r['points_now'])

        return render(request, self.template_name, {
            'team': team,
            'rows': rows,
            'month': month,
            'months': MONTHS_IT,
        })


# ---------------- Feed iCal della lega ----------------

def _ical_escape(text):
    return (text or '').replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')


def _ical_dt(d):
    return d.strftime('%Y%m%d')


class LeagueCalendarView(LoginRequiredMixin, View):
    """Esporta gli eventi-chiave della lega come feed iCalendar (RFC 5545).

    Eventi: apertura/chiusura iscrizioni, inizio/fine stagione, e per ogni
    membro morto non ancora sostituito una scadenza di sostituzione.
    """

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return HttpResponseForbidden('Non hai accesso a questa lega.')

        lines = [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//Fantamorte//IT',
            f'X-WR-CALNAME:Fantamorte — {_ical_escape(league.name)}',
        ]

        def add_event(uid, summary, dtstart, dtend=None):
            lines.append('BEGIN:VEVENT')
            lines.append(f'UID:{uid}@fantamorte')
            lines.append(f'DTSTAMP:{timezone.now().strftime("%Y%m%dT%H%M%SZ")}')
            lines.append(f'DTSTART;VALUE=DATE:{_ical_dt(dtstart)}')
            if dtend is not None:
                lines.append(f'DTEND;VALUE=DATE:{_ical_dt(dtend)}')
            lines.append(f'SUMMARY:{_ical_escape(summary)}')
            lines.append('END:VEVENT')

        add_event(f'league-{league.id}-reg-open', f'Apertura iscrizioni — {league.name}',
                  league.registration_opens)
        add_event(f'league-{league.id}-reg-close', f'Chiusura iscrizioni — {league.name}',
                  league.registration_closes)
        add_event(f'league-{league.id}-start', f'Inizio stagione — {league.name}',
                  league.start_date)
        add_event(f'league-{league.id}-end', f'Fine stagione — {league.name}',
                  league.end_date)

        # Scadenze di sostituzione per membri morti non ancora sostituiti
        member_qs = TeamMember.objects.filter(
            team__league=league,
            replaced_by__isnull=True,
            person__death__is_confirmed=True,
        ).select_related('person', 'person__death', 'team', 'team__manager')
        # Filtro lato Python perché get_substitution_deadline è un metodo
        for m in member_qs:
            deadline = m.get_substitution_deadline()
            if deadline is None:
                continue
            add_event(
                f'league-{league.id}-sub-{m.id}',
                f'Scadenza sostituzione {m.person.name_it} ({m.team.manager.username})',
                deadline.date(),
            )

        lines.append('END:VCALENDAR')
        body = '\r\n'.join(lines) + '\r\n'
        resp = HttpResponse(body, content_type='text/calendar; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="fantamorte-{league.slug}.ics"'
        return resp


# ---------------- Export CSV ----------------

class LeagueRankingsCSVView(LoginRequiredMixin, View):
    """Scarica la classifica corrente della lega in CSV."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return HttpResponseForbidden('Non hai accesso a questa lega.')

        rankings = scoring.compute_league_rankings(league)
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="classifica-{league.slug}.csv"'
        writer = csv.writer(resp)
        writer.writerow(['posizione', 'squadra', 'manager', 'punteggio', 'decessi_validi'])
        for i, row in enumerate(rankings, start=1):
            t = row['team']
            writer.writerow([i, t.name, t.manager.username, row['score'], len(row['deaths'])])
        return resp


class LeagueDeathsCSVView(LoginRequiredMixin, View):
    """Scarica la timeline decessi della lega in CSV."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return HttpResponseForbidden('Non hai accesso a questa lega.')

        deaths = (
            Death.objects.filter(
                is_confirmed=True,
                death_date__gte=league.start_date,
                death_date__lte=league.end_date,
            )
            .select_related('person')
            .prefetch_related('bonuses__bonus_type')
            .order_by('death_date')
        )
        resp = HttpResponse(content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="decessi-{league.slug}.csv"'
        writer = csv.writer(resp)
        writer.writerow(['data', 'nome', 'eta', 'wikidata_id', 'bonus'])
        for d in deaths:
            bonus_names = ', '.join(b.bonus_type.name for b in d.bonuses.all())
            writer.writerow([
                d.death_date.isoformat(),
                d.person.name_it,
                d.death_age if d.death_age is not None else '',
                d.person.wikidata_id,
                bonus_names,
            ])
        return resp
