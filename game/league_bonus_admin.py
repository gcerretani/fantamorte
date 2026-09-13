from django.contrib import admin

from .league_bonus_decisions import LeagueDeathBonus


@admin.register(LeagueDeathBonus)
class LeagueDeathBonusAdmin(admin.ModelAdmin):
    list_display = ('league', 'death', 'bonus_type', 'created_by', 'updated_by', 'updated_at')
    list_filter = ('league', 'bonus_type')
    search_fields = ('league__name', 'death__person__name_it', 'bonus_type__name', 'reason')
    raw_id_fields = ('death', 'created_by', 'updated_by')
    autocomplete_fields = ('league', 'bonus_type')
    readonly_fields = ('created_at', 'updated_at')
