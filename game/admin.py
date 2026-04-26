from django.contrib import admin
from django.utils import timezone
from django.contrib import messages
from .models import (
    Season, WikipediaPerson, BonusType, Team, TeamMember,
    Death, DeathBonus, UserProfile, PushSubscription,
    League, LeagueMembership, LeagueBonus,
)
from . import scoring


class LeagueMembershipInline(admin.TabularInline):
    model = LeagueMembership
    extra = 0
    fields = ('user', 'role', 'joined_at')
    readonly_fields = ('joined_at',)


class LeagueBonusInline(admin.TabularInline):
    model = LeagueBonus
    extra = 0
    fields = ('bonus_type', 'is_active', 'override_points', 'override_formula')
    autocomplete_fields = ('bonus_type',)


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'visibility', 'start_date', 'end_date',
                    'max_non_captains', 'substitution_deadline_days', 'is_locked')
    list_filter = ('visibility', 'is_locked')
    search_fields = ('name', 'slug', 'owner__username')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [LeagueMembershipInline, LeagueBonusInline]


@admin.register(LeagueMembership)
class LeagueMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'league', 'role', 'joined_at')
    list_filter = ('role',)
    search_fields = ('user__username', 'league__name')


@admin.register(LeagueBonus)
class LeagueBonusAdmin(admin.ModelAdmin):
    list_display = ('league', 'bonus_type', 'is_active', 'override_points', 'override_formula')
    list_filter = ('is_active', 'league')
    search_fields = ('league__name', 'bonus_type__name')


class DeathBonusInline(admin.TabularInline):
    model = DeathBonus
    extra = 1
    fields = ('bonus_type', 'points_awarded', 'is_auto_detected', 'notes')


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    extra = 0
    fields = ('person', 'is_captain', 'is_original', 'replaced_by')
    raw_id_fields = ('person', 'replaced_by')
    readonly_fields = ('added_at',)
    fk_name = 'team'


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = (
        'year', 'is_active', 'registration_opens', 'registration_closes',
        'substitution_deadline_days', 'death_count',
    )
    list_filter = ('is_active',)
    list_editable = ('substitution_deadline_days',)
    actions = ['set_active']

    def death_count(self, obj):
        return obj.deaths.filter(is_confirmed=True).count()
    death_count.short_description = 'Morti confermati'

    @admin.action(description='Imposta come stagione attiva')
    def set_active(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, 'Seleziona esattamente una stagione.', messages.ERROR)
            return
        Season.objects.update(is_active=False)
        queryset.update(is_active=True)
        self.message_user(request, f'Stagione {queryset.first().year} impostata come attiva.')


@admin.register(WikipediaPerson)
class WikidataPersonAdmin(admin.ModelAdmin):
    list_display = ('name_it', 'wikidata_id', 'birth_date', 'death_date', 'is_dead', 'occupation', 'last_checked')
    list_filter = ('is_dead',)
    search_fields = ('name_it', 'wikidata_id')
    readonly_fields = ('last_checked', 'summary_fetched_at', 'claims_cache')
    actions = ['refresh_from_wikidata']

    @admin.action(description='Aggiorna da Wikidata')
    def refresh_from_wikidata(self, request, queryset):
        from wikidata_api.client import WikidataClient
        client = WikidataClient()
        updated = 0
        for person in queryset:
            try:
                entity = client.get_entity(person.wikidata_id)
                person.name_it = entity['name_it']
                person.name_en = entity.get('name_en', '')
                person.birth_date = entity.get('birth_date')
                person.birth_year = entity.get('birth_year')
                person.death_date = entity.get('death_date')
                person.death_year = entity.get('death_year')
                person.is_dead = entity.get('death_date') is not None or entity.get('death_year') is not None
                person.claims_cache = entity.get('claims_cache', {})
                person.last_checked = timezone.now()
                person.save()
                updated += 1
            except Exception as e:
                self.message_user(request, f'Errore per {person.wikidata_id}: {e}', messages.WARNING)
        self.message_user(request, f'{updated} persone aggiornate.')


@admin.register(BonusType)
class BonusTypeAdmin(admin.ModelAdmin):
    list_display = ('name', 'points', 'points_formula', 'detection_method',
                    'wikidata_property', 'wikidata_value', 'age_formula',
                    'is_active', 'ordering')
    list_editable = ('ordering', 'is_active', 'points')
    list_filter = ('detection_method', 'is_active')
    search_fields = ('name',)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'manager', 'league', 'jolly_month', 'member_count', 'team_score', 'is_locked')
    list_filter = ('league', 'is_locked')
    search_fields = ('name', 'manager__username', 'league__name')
    autocomplete_fields = ('league',)
    raw_id_fields = ('manager',)
    inlines = [TeamMemberInline]
    readonly_fields = ('team_score',)

    def member_count(self, obj):
        return obj.members.filter(replaced_by=None).count()
    member_count.short_description = 'Membri attivi'

    def team_score(self, obj):
        return scoring.compute_team_total_score(obj)
    team_score.short_description = 'Punteggio'


@admin.register(Death)
class DeathAdmin(admin.ModelAdmin):
    list_display = ('person', 'death_date', 'death_age', 'season', 'source', 'is_confirmed', 'confirmed_by')
    list_filter = ('season', 'is_confirmed', 'source')
    search_fields = ('person__name_it',)
    readonly_fields = ('created_at',)
    inlines = [DeathBonusInline]
    actions = ['confirm_deaths', 'detect_bonuses_action']

    @admin.action(description='Conferma morti selezionati')
    def confirm_deaths(self, request, queryset):
        count = 0
        for death in queryset.filter(is_confirmed=False):
            death.is_confirmed = True
            death.confirmed_at = timezone.now()
            death.confirmed_by = request.user
            death.save()
            death.person.is_dead = True
            death.person.save()
            count += 1
        self.message_user(request, f'{count} decessi confermati.')

    @admin.action(description='Auto-rileva bonus da Wikidata')
    def detect_bonuses_action(self, request, queryset):
        from wikidata_api.client import WikidataClient
        client = WikidataClient()
        bonus_types = BonusType.objects.filter(
            is_active=True,
            detection_method__in=['wikidata', 'age']
        )
        total = 0
        for death in queryset:
            person = death.person
            detected = client.detect_bonuses(person.wikidata_id, person.claims_cache, bonus_types)
            for bt in detected:
                _, created = DeathBonus.objects.get_or_create(
                    death=death, bonus_type=bt,
                    defaults={'points_awarded': bt.points, 'is_auto_detected': True}
                )
                if created:
                    total += 1
            # Age-based
            age = person.get_age_at_death()
            if age is not None:
                for bt in bonus_types.filter(detection_method='age'):
                    if client.detect_age_bonus(age, bt):
                        _, created = DeathBonus.objects.get_or_create(
                            death=death, bonus_type=bt,
                            defaults={'points_awarded': bt.points, 'is_auto_detected': True}
                        )
                        if created:
                            total += 1
        self.message_user(request, f'{total} bonus rilevati.')


@admin.register(DeathBonus)
class DeathBonusAdmin(admin.ModelAdmin):
    list_display = ('death', 'bonus_type', 'points_awarded', 'is_auto_detected')
    list_filter = ('bonus_type', 'is_auto_detected')
    search_fields = ('death__person__name_it',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'push_notifications_enabled', 'email_notifications_enabled', 'dark_mode')
    list_filter = ('push_notifications_enabled', 'email_notifications_enabled')
    search_fields = ('user__username', 'user__email')


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'endpoint_short', 'user_agent', 'created_at', 'last_used_at')
    search_fields = ('user__username', 'endpoint')
    readonly_fields = ('created_at', 'last_used_at')

    def endpoint_short(self, obj):
        return obj.endpoint[:60] + '…' if len(obj.endpoint) > 60 else obj.endpoint
    endpoint_short.short_description = 'Endpoint'
