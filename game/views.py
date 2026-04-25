from django.views.generic import TemplateView, DetailView, View
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.contrib import messages
from django.utils import timezone
from .models import Season, Team, TeamMember, WikipediaPerson, Death, DeathBonus, BonusType
from . import scoring


class HomeView(TemplateView):
    template_name = 'game/home.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        season = Season.objects.filter(is_active=True).first()
        ctx['season'] = season
        if season:
            ctx['rankings'] = scoring.compute_season_rankings(season)[:3]
            ctx['recent_deaths'] = (
                Death.objects.filter(season=season, is_confirmed=True)
                .select_related('person')
                .order_by('-death_date')[:5]
            )
        return ctx


class RankingsView(TemplateView):
    template_name = 'game/rankings.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        year = kwargs.get('year')
        if year:
            season = get_object_or_404(Season, year=year)
        else:
            season = Season.objects.filter(is_active=True).first()
        ctx['season'] = season
        ctx['all_seasons'] = Season.objects.all()
        if season:
            ctx['rankings'] = scoring.compute_season_rankings(season)
        return ctx


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


class TeamCreateView(LoginRequiredMixin, View):
    template_name = 'game/team_edit.html'

    def _get_season(self):
        return Season.objects.filter(is_active=True).first()

    def get(self, request):
        season = self._get_season()
        if not season:
            messages.error(request, 'Nessuna stagione attiva.')
            return redirect('home')
        if not season.is_registration_open():
            messages.error(request, 'Le registrazioni non sono aperte.')
            return redirect('home')
        existing = Team.objects.filter(manager=request.user, season=season).first()
        if existing:
            return redirect('team_edit', pk=existing.pk)
        return render(request, self.template_name, {'season': season, 'creating': True})

    def post(self, request):
        season = self._get_season()
        if not season or not season.is_registration_open():
            messages.error(request, 'Le registrazioni non sono aperte.')
            return redirect('home')
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Il nome della squadra è obbligatorio.')
            return render(request, self.template_name, {'season': season, 'creating': True})
        team, created = Team.objects.get_or_create(
            manager=request.user, season=season,
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
        season = team.season
        members = team.members.select_related('person').order_by('-is_captain', 'person__name_it')
        dead_members = [m for m in members if m.person.is_dead and m.is_active()]
        return render(request, self.template_name, {
            'team': team,
            'season': season,
            'members': members,
            'dead_members': dead_members,
            'months': [(i, n) for i, n in [
                (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
                (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
                (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
            ]],
            'can_edit': season.is_registration_open() or request.user.is_staff,
        })

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        if team.manager != request.user and not request.user.is_staff:
            return redirect('team_detail', pk=pk)
        season = team.season
        if not season.is_registration_open() and not request.user.is_staff:
            messages.error(request, 'Non è più possibile modificare la squadra.')
            return redirect('team_edit', pk=pk)

        name = request.POST.get('name', '').strip()
        jolly_month = request.POST.get('jolly_month')
        captain_id = request.POST.get('captain_id')

        if name:
            team.name = name
        if jolly_month:
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
        season = team.season
        if not season.is_registration_open() and not request.user.is_staff:
            return JsonResponse({'error': 'Registrazioni chiuse'}, status=400)

        wikidata_id = request.POST.get('wikidata_id', '').strip()
        is_captain = request.POST.get('is_captain') == '1'

        if not wikidata_id:
            return JsonResponse({'error': 'wikidata_id mancante'}, status=400)

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
                'birth_date': entity.get('birth_date'),
                'birth_year': entity.get('birth_year'),
                'death_date': entity.get('death_date'),
                'is_dead': entity.get('death_date') is not None or entity.get('death_year') is not None,
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
            if active_captain >= 1:
                return JsonResponse({'error': 'La squadra ha già un capitano.'}, status=400)
        else:
            if active_non_captain >= 11:
                return JsonResponse({'error': 'La squadra ha già 11 morituri.'}, status=400)

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
        return render(request, self.template_name, {'team': team, 'member': member})

    def post(self, request, pk, member_pk):
        team = get_object_or_404(Team, pk=pk)
        member = get_object_or_404(TeamMember, pk=member_pk, team=team)
        if team.manager != request.user and not request.user.is_staff:
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
                'birth_date': entity.get('birth_date'),
                'birth_year': entity.get('birth_year'),
                'death_date': entity.get('death_date'),
                'is_dead': entity.get('death_date') is not None,
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
