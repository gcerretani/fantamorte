import json
import secrets
from django.views.generic import TemplateView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib import messages
from django.utils import timezone
from django.utils.text import slugify
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.cache import cache_control
from django.conf import settings
from django.urls import reverse
from .models import (
    Team, TeamMember, WikipediaPerson, Death, BonusType,
    UserProfile, PushSubscription, League, LeagueMembership, LeagueBonus,
    SiteConfiguration,
)
from . import scoring


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
        my_leagues = []
        for m in my_memberships:
            team = Team.objects.filter(manager=user, league=m.league).first()
            my_leagues.append({'league': m.league, 'role': m.role, 'team': team})
        ctx['my_leagues'] = my_leagues
        # Suggerimenti: leghe pubbliche di cui non sono membro
        member_ids = [m.league_id for m in my_memberships]
        ctx['suggested_leagues'] = (
            League.objects.filter(visibility=League.VISIBILITY_PUBLIC)
            .exclude(pk__in=member_ids)
            .order_by('-start_date')[:5]
        )
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

        slug = _unique_slug(name)
        try:
            league = League.objects.create(
                name=name,
                slug=slug,
                description=request.POST.get('description', '').strip(),
                owner=request.user,
                visibility=request.POST.get('visibility', League.VISIBILITY_PUBLIC),
                invite_code=secrets.token_urlsafe(8) if request.POST.get('visibility') == League.VISIBILITY_PRIVATE else '',
                start_date=request.POST.get('start_date') or timezone.now().date(),
                end_date=request.POST.get('end_date') or timezone.now().date(),
                registration_opens=request.POST.get('registration_opens') or timezone.now().date(),
                registration_closes=request.POST.get('registration_closes') or timezone.now().date(),
                base_points=int(request.POST.get('base_points') or 50),
                captain_multiplier=int(request.POST.get('captain_multiplier') or 2),
                jolly_multiplier=int(request.POST.get('jolly_multiplier') or 2),
                jolly_enabled=request.POST.get('jolly_enabled') == 'on',
                max_captains=int(request.POST.get('max_captains') or 1),
                max_non_captains=int(request.POST.get('max_non_captains') or 11),
                substitution_deadline_days=int(request.POST.get('substitution_deadline_days') or 7),
            )
        except (ValueError, TypeError) as e:
            messages.error(request, f'Dati non validi: {e}')
            return render(request, self.template_name, {'creating': True, 'data': request.POST})

        # Iscrizione owner + bonus default attivati
        LeagueMembership.objects.create(league=league, user=request.user, role=LeagueMembership.ROLE_OWNER)
        for bt in BonusType.objects.filter(is_active=True):
            LeagueBonus.objects.create(league=league, bonus_type=bt, is_active=True)

        messages.success(request, f'Lega "{league.name}" creata!')
        return redirect('league_detail', slug=league.slug)


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
            messages.error(request, 'Lega privata: serve un invito.')
            return redirect('league_list')

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
            'all_bonus_types': BonusType.objects.all().order_by('ordering'),
            'is_owner': league.is_owner(request.user),
        })

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            return HttpResponseForbidden('Permesso negato.')
        action = request.POST.get('action', '')

        if action == 'update_rules':
            league.name = request.POST.get('name', league.name).strip() or league.name
            league.description = request.POST.get('description', league.description)
            league.visibility = request.POST.get('visibility', league.visibility)
            league.start_date = request.POST.get('start_date') or league.start_date
            league.end_date = request.POST.get('end_date') or league.end_date
            league.registration_opens = request.POST.get('registration_opens') or league.registration_opens
            league.registration_closes = request.POST.get('registration_closes') or league.registration_closes
            try:
                league.base_points = int(request.POST.get('base_points') or league.base_points)
                league.captain_multiplier = int(request.POST.get('captain_multiplier') or league.captain_multiplier)
                league.jolly_multiplier = int(request.POST.get('jolly_multiplier') or league.jolly_multiplier)
                league.max_captains = int(request.POST.get('max_captains') or league.max_captains)
                league.max_non_captains = int(request.POST.get('max_non_captains') or league.max_non_captains)
                league.substitution_deadline_days = int(request.POST.get('substitution_deadline_days') or league.substitution_deadline_days)
            except (ValueError, TypeError):
                messages.error(request, 'Valori numerici non validi.')
                return redirect('league_admin', slug=slug)
            league.jolly_enabled = request.POST.get('jolly_enabled') == 'on'
            league.is_locked = request.POST.get('is_locked') == 'on'
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
                lb.override_points = int(pts) if pts else None
                lb.save()
            # Eventuali nuovi bonus type
            for bt_id in request.POST.getlist('add_bonus'):
                try:
                    bt = BonusType.objects.get(pk=int(bt_id))
                except (BonusType.DoesNotExist, ValueError, TypeError):
                    continue
                LeagueBonus.objects.get_or_create(league=league, bonus_type=bt, defaults={'is_active': True})
            messages.success(request, 'Bonus aggiornati.')

        elif action == 'promote_admin':
            if not league.is_owner(request.user):
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
            if not league.is_owner(request.user):
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
            if not league.is_owner(request.user):
                messages.error(request, 'Solo il proprietario può trasferire la proprietà.')
                return redirect('league_admin', slug=slug)
            try:
                m = league.memberships.get(pk=int(request.POST.get('membership_id')))
            except (LeagueMembership.DoesNotExist, ValueError, TypeError):
                messages.error(request, 'Iscrizione non trovata.')
                return redirect('league_admin', slug=slug)
            old_owner_membership = league.memberships.get(user=request.user)
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
        })


class LeagueDeathsView(LoginRequiredMixin, View):
    """Cronologia decessi di una lega."""

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.can_user_view(request.user):
            return redirect('league_list')
        deaths = (
            Death.objects.filter(
                is_confirmed=True,
                death_date__gte=league.start_date,
                death_date__lte=league.end_date,
            )
            .select_related('person')
            .prefetch_related('bonuses__bonus_type')
            .order_by('-death_date')
        )
        return render(request, 'game/league_deaths.html', {'league': league, 'deaths': deaths})


# ---------------- Squadre ----------------

class TeamDetailView(DetailView):
    model = Team
    template_name = 'game/team_detail.html'
    context_object_name = 'team'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        team = self.object
        ctx['score'] = scoring.compute_team_total_score(team)
        ctx['death_details'] = scoring.compute_team_death_details(team)
        ctx['active_members'] = team.get_active_members().select_related('person')
        ctx['all_members'] = team.members.select_related('person', 'replaced_by__person')
        return ctx


class DeathDetailView(DetailView):
    model = Death
    template_name = 'game/death_detail.html'
    context_object_name = 'death'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        death = self.object
        ctx['bonuses'] = death.bonuses.select_related('bonus_type')
        ctx['teams_affected'] = []
        for member in TeamMember.objects.filter(person=death.person).select_related('team'):
            pts = scoring.compute_team_points_for_death(member.team, death)
            if pts:
                ctx['teams_affected'].append({'team': member.team, 'points': pts})
        return ctx


MONTHS_LIST = [
    (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
    (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
    (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
]


def _can_edit_team(team, user):
    if user.is_staff:
        return True
    if team.manager_id != user.pk:
        return False
    if team.league_id:
        return team.league.is_registration_open() and not team.league.is_locked
    if team.season_id:
        return team.season.is_registration_open()
    return False


class TeamCreateView(LoginRequiredMixin, View):
    """Crea (o redirect a) la squadra dell'utente in una specifica lega."""
    template_name = 'game/team_edit.html'

    def get(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_member(request.user):
            messages.error(request, 'Devi prima iscriverti alla lega.')
            return redirect('league_detail', slug=slug)
        if not league.is_registration_open() and not request.user.is_staff:
            messages.error(request, 'Le registrazioni non sono aperte per questa lega.')
            return redirect('league_detail', slug=slug)
        existing = Team.objects.filter(manager=request.user, league=league).first()
        if existing:
            return redirect('team_edit', pk=existing.pk)
        return render(request, self.template_name, {'league': league, 'creating': True})

    def post(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_member(request.user):
            messages.error(request, 'Devi prima iscriverti alla lega.')
            return redirect('league_detail', slug=slug)
        if not league.is_registration_open() and not request.user.is_staff:
            messages.error(request, 'Le registrazioni non sono aperte.')
            return redirect('league_detail', slug=slug)
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Il nome della squadra è obbligatorio.')
            return render(request, self.template_name, {'league': league, 'creating': True})
        team, created = Team.objects.get_or_create(
            manager=request.user, league=league,
            defaults={'name': name}
        )
        if not created:
            team.name = name
            team.save()
        return redirect('team_edit', pk=team.pk)


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
        return render(request, self.template_name, {
            'team': team,
            'league': league,
            'season': team.season,  # legacy
            'members': members,
            'dead_members': dead_members,
            'months': MONTHS_LIST,
            'can_edit': _can_edit_team(team, request.user),
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
        jolly_month = request.POST.get('jolly_month')
        captain_id = request.POST.get('captain_id')

        if name:
            team.name = name
        if jolly_month and (not team.league or team.league.jolly_enabled):
            team.jolly_month = int(jolly_month)
        team.save()

        if captain_id:
            team.members.update(is_captain=False)
            team.members.filter(pk=int(captain_id)).update(is_captain=True)

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

        # Usa la cache DB se il concorrente è già presente; altrimenti recupera da Wikidata
        person = WikipediaPerson.objects.filter(wikidata_id=wikidata_id).first()
        if not person:
            from wikidata_api.client import WikidataClient
            client = WikidataClient()
            try:
                entity = client.get_entity(wikidata_id)
            except Exception as e:
                return JsonResponse({'error': f'Errore Wikidata: {e}'}, status=500)

            person, _ = WikipediaPerson.objects.update_or_create(
                wikidata_id=wikidata_id,
                defaults={
                    'name_it': entity['name_it'],
                    'name_en': entity.get('name_en', ''),
                    'description_it': entity.get('description_it', ''),
                    'birth_date': entity.get('birth_date'),
                    'birth_year': entity.get('birth_year'),
                    'death_date': entity.get('death_date'),
                    'is_dead': entity.get('death_date') is not None or entity.get('death_year') is not None,
                    'image_url': entity.get('image_url', ''),
                    'occupation': entity.get('occupation', ''),
                    'nationality': entity.get('nationality', ''),
                    'claims_cache': entity.get('claims_cache', {}),
                    'wikipedia_url_it': entity.get('wikipedia_url_it', ''),
                    'last_checked': timezone.now(),
                }
            )

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

        member = TeamMember.objects.create(team=team, person=person, is_captain=is_captain)
        return JsonResponse({
            'success': True,
            'member_id': member.pk,
            'name': person.name_it,
            'wikidata_id': person.wikidata_id,
            'is_captain': is_captain,
        })


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
        if not member.can_be_substituted() and not request.user.is_staff:
            days = (team.league.substitution_deadline_days if team.league_id else
                    (team.season.substitution_deadline_days if team.season_id else 7))
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
        if not member.can_be_substituted() and not request.user.is_staff:
            messages.error(request, 'I tempi per la sostituzione sono scaduti.')
            return redirect('team_edit', pk=pk)

        wikidata_id = request.POST.get('wikidata_id', '').strip()
        if not wikidata_id:
            messages.error(request, 'Seleziona una persona da Wikidata.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        from wikidata_api.client import WikidataClient
        client = WikidataClient()
        try:
            entity = client.get_entity(wikidata_id)
        except Exception as e:
            messages.error(request, f'Errore Wikidata: {e}')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        person, _ = WikipediaPerson.objects.update_or_create(
            wikidata_id=wikidata_id,
            defaults={
                'name_it': entity['name_it'],
                'name_en': entity.get('name_en', ''),
                'description_it': entity.get('description_it', ''),
                'birth_date': entity.get('birth_date'),
                'birth_year': entity.get('birth_year'),
                'death_date': entity.get('death_date'),
                'is_dead': entity.get('death_date') is not None,
                'image_url': entity.get('image_url', ''),
                'occupation': entity.get('occupation', ''),
                'nationality': entity.get('nationality', ''),
                'claims_cache': entity.get('claims_cache', {}),
                'wikipedia_url_it': entity.get('wikipedia_url_it', ''),
                'last_checked': timezone.now(),
            }
        )

        if person.is_dead:
            messages.error(request, f'{person.name_it} è già morto/a.')
            return redirect('substitute_member', pk=pk, member_pk=member_pk)

        new_member = TeamMember.objects.create(
            team=team, person=person, is_captain=member.is_captain
        )
        member.replaced_by = new_member
        member.save()

        messages.success(request, f'{member.person.name_it} sostituito/a con {person.name_it}.')
        return redirect('team_edit', pk=pk)


class PersonDetailView(DetailView):
    """Pagina di dettaglio di una persona della rosa."""
    model = WikipediaPerson
    template_name = 'game/person_detail.html'
    context_object_name = 'person'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        person = self.object
        # Lazy fetch del summary se mancante
        if not person.summary_it and person.wikipedia_url_it:
            try:
                from wikidata_api.client import WikidataClient
                from urllib.parse import unquote
                title = unquote(person.wikipedia_url_it.rsplit('/', 1)[-1].replace('_', ' '))
                client = WikidataClient()
                summary = client.get_summary(title)
                if summary:
                    person.summary_it = summary
                    person.summary_fetched_at = timezone.now()
                    person.save(update_fields=['summary_it', 'summary_fetched_at'])
            except Exception:
                pass
        ctx['team_members'] = TeamMember.objects.filter(person=person).select_related('team__manager', 'team__season')
        return ctx


class PersonInfoView(View):
    """Endpoint JSON per il pannello dettagli persona (open su click)."""

    def get(self, request, pk):
        person = get_object_or_404(WikipediaPerson, pk=pk)
        # Aggiorna summary se mancante o stantio (>30 giorni)
        try:
            need_refresh = not person.summary_it
            if not need_refresh and person.summary_fetched_at:
                from datetime import timedelta
                need_refresh = (timezone.now() - person.summary_fetched_at) > timedelta(days=30)
            if need_refresh and person.wikipedia_url_it:
                from wikidata_api.client import WikidataClient
                from urllib.parse import unquote
                title = unquote(person.wikipedia_url_it.rsplit('/', 1)[-1].replace('_', ' '))
                client = WikidataClient()
                summary = client.get_summary(title)
                if summary:
                    person.summary_it = summary
                    person.summary_fetched_at = timezone.now()
                    person.save(update_fields=['summary_it', 'summary_fetched_at'])
        except Exception:
            pass

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
            'wikidata_url': f'https://www.wikidata.org/wiki/{person.wikidata_id}',
        }
        return JsonResponse(data)


class PersonSearchView(View):
    def get(self, request):
        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'results': []})
        from wikidata_api.client import WikidataClient
        client = WikidataClient()
        try:
            results = client.search_by_italian_name(q)
        except Exception:
            results = []
        return JsonResponse({'results': results})


class RulesView(TemplateView):
    template_name = 'game/rules.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['bonus_types'] = BonusType.objects.filter(is_active=True).order_by('ordering', 'name')
        return ctx


class ProfileView(LoginRequiredMixin, View):
    template_name = 'game/profile.html'

    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        subs = request.user.push_subscriptions.all()
        return render(request, self.template_name, {
            'profile': profile,
            'push_subscriptions': subs,
            'team': request.user.teams.first(),
        })

    def post(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.push_notifications_enabled = request.POST.get('push_notifications_enabled') == 'on'
        profile.email_notifications_enabled = request.POST.get('email_notifications_enabled') == 'on'
        profile.dark_mode = request.POST.get('dark_mode') == 'on'
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
            'icons': [
                {'src': '/static/pwa/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any maskable'},
                {'src': '/static/pwa/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any maskable'},
                {'src': '/static/pwa/icon.svg', 'sizes': 'any', 'type': 'image/svg+xml', 'purpose': 'any'},
            ],
            'shortcuts': [
                {'name': 'Classifica', 'url': '/classifica/'},
                {'name': 'La mia squadra', 'url': '/profilo/'},
                {'name': 'Decessi', 'url': '/decessi/'},
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


# --- Push subscriptions API ---

@method_decorator(csrf_exempt, name='dispatch')
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


@method_decorator(csrf_exempt, name='dispatch')
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
# Aggiornamento dati Wikidata concorrenti (diff / apply)
# ---------------------------------------------------------------------------

_PERSON_TRACKED_FIELDS = [
    'name_it', 'name_en', 'description_it',
    'birth_date', 'birth_year',
    'death_date', 'death_year', 'is_dead',
    'image_url', 'occupation', 'nationality',
    'wikipedia_url_it',
]

_FIELD_LABELS = {
    'name_it': 'Nome (it)',
    'name_en': 'Nome (en)',
    'description_it': 'Descrizione',
    'birth_date': 'Data di nascita',
    'birth_year': 'Anno di nascita',
    'death_date': 'Data di morte',
    'death_year': 'Anno di morte',
    'is_dead': 'Deceduto',
    'image_url': 'Immagine URL',
    'occupation': 'Professione',
    'nationality': 'Nazionalità',
    'wikipedia_url_it': 'Wikipedia (it)',
}


def _compute_person_diff(person, entity):
    """Restituisce una lista di dict {field, label, old, new} per i campi cambiati."""
    changes = []
    for field in _PERSON_TRACKED_FIELDS:
        old_val = getattr(person, field)
        # is_dead è derivato nell'entity dal presence di death_date/death_year
        if field == 'is_dead':
            new_val = (
                entity.get('death_date') is not None
                or entity.get('death_year') is not None
            )
        else:
            new_val = entity.get(field)
        if str(old_val) != str(new_val):
            changes.append({
                'field': field,
                'label': _FIELD_LABELS.get(field, field),
                'old': old_val,
                'new': new_val,
            })
    if person.claims_cache != entity.get('claims_cache', {}):
        changes.append({
            'field': 'claims_cache',
            'label': 'Claims Wikidata',
            'old': '(vedi Wikidata)',
            'new': '(aggiornato)',
            'claims_only': True,
        })
    return changes


class PersonUpdatesView(LoginRequiredMixin, View):
    """Pannello per il confronto e l'applicazione degli aggiornamenti Wikidata."""

    template_name = 'game/person_updates.html'

    def _get_league_and_check_admin(self, request, slug):
        league = get_object_or_404(League, slug=slug)
        if not league.is_admin(request.user):
            raise PermissionError
        return league

    def _active_persons(self, league):
        return WikipediaPerson.objects.filter(
            team_members__team__league=league,
            team_members__replaced_by__isnull=True,
        ).distinct().order_by('name_it')

    def get(self, request, slug):
        try:
            league = self._get_league_and_check_admin(request, slug)
        except PermissionError:
            return HttpResponseForbidden()
        persons = self._active_persons(league)
        return render(request, self.template_name, {
            'league': league,
            'persons': persons,
            'mode': 'list',
            'many_warning': persons.count() > 50,
        })

    def post(self, request, slug):
        try:
            league = self._get_league_and_check_admin(request, slug)
        except PermissionError:
            return HttpResponseForbidden()

        action = request.POST.get('action')

        if action == 'check':
            return self._handle_check(request, league)
        if action == 'apply':
            return self._handle_apply(request, league)

        return redirect('league_admin', slug=slug)

    def _handle_check(self, request, league):
        from wikidata_api.client import WikidataClient
        client = WikidataClient()

        all_persons = self._active_persons(league)
        if request.POST.get('check_all'):
            persons_to_check = list(all_persons)
        else:
            pks = request.POST.getlist('person_pks')
            persons_to_check = list(all_persons.filter(pk__in=pks))

        results = []
        for person in persons_to_check:
            try:
                entity = client.get_entity(person.wikidata_id)
            except Exception as e:
                results.append({
                    'person': person,
                    'error': str(e),
                    'changes': [],
                    'fresh_json': '',
                })
                continue
            changes = _compute_person_diff(person, entity)
            # Serializza solo i campi tracciati + claims_cache per l'apply
            fresh_data = {f: entity.get(f) for f in _PERSON_TRACKED_FIELDS}
            fresh_data['is_dead'] = (
                entity.get('death_date') is not None
                or entity.get('death_year') is not None
            )
            fresh_data['claims_cache'] = entity.get('claims_cache', {})
            results.append({
                'person': person,
                'error': None,
                'changes': changes,
                'fresh_json': json.dumps(fresh_data, default=str),
            })

        return render(request, self.template_name, {
            'league': league,
            'results': results,
            'mode': 'diff',
        })

    def _handle_apply(self, request, league):
        all_persons = self._active_persons(league)
        applied = []

        # Trova tutti i person_pk nascosti nel form
        person_pks = {
            key.split('_')[1]
            for key in request.POST
            if key.startswith('fresh_json_')
        }

        for pk in person_pks:
            raw = request.POST.get(f'fresh_json_{pk}', '')
            if not raw:
                continue
            try:
                fresh_data = json.loads(raw)
            except ValueError:
                continue

            try:
                person = all_persons.get(pk=pk)
            except WikipediaPerson.DoesNotExist:
                continue

            updated_fields = []
            for field in _PERSON_TRACKED_FIELDS + ['claims_cache']:
                if request.POST.get(f'apply_{pk}_{field}'):
                    new_val = fresh_data.get(field)
                    setattr(person, field, new_val)
                    updated_fields.append(_FIELD_LABELS.get(field, field))

            if updated_fields:
                person.last_checked = timezone.now()
                person.save()
                applied.append({'person': person, 'fields': updated_fields})

        return render(request, self.template_name, {
            'league': league,
            'applied': applied,
            'mode': 'result',
        })
