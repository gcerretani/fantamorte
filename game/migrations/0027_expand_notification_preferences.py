from django.db import migrations


EVENT_DEFAULTS = {
    'death': {'push': True, 'email': True},
    'death_team': {'push': True, 'email': True},
    'preseason_removed': {'push': True, 'email': True},
    'substitution_reminder': {'push': True, 'email': True},
    'league_joined': {'push': False, 'email': False},
    'league_started': {'push': False, 'email': False},
    'league_ended': {'push': False, 'email': False},
    'team_locked': {'push': False, 'email': False},
}

LEGACY_CATEGORY_BY_EVENT = {
    'death': 'death',
    'death_team': 'death',
    'substitution_reminder': 'substitution',
    'preseason_removed': 'substitution',
    'league_joined': 'league_joined',
    'league_started': 'league_events',
    'league_ended': 'league_events',
    'team_locked': 'league_events',
}


def forwards(apps, schema_editor):
    UserProfile = apps.get_model('game', 'UserProfile')
    for profile in UserProfile.objects.all().iterator():
        source = dict(profile.notification_prefs or {})
        result = dict(source)
        changed = False
        for event, legacy in LEGACY_CATEGORY_BY_EVENT.items():
            if event in result:
                continue
            legacy_state = source.get(legacy)
            defaults = EVENT_DEFAULTS[event]
            if isinstance(legacy_state, dict):
                result[event] = {
                    'push': bool(legacy_state.get('push', defaults['push'])),
                    'email': bool(legacy_state.get('email', defaults['email'])),
                }
            else:
                result[event] = dict(defaults)
            changed = True
        if changed:
            profile.notification_prefs = result
            profile.save(update_fields=['notification_prefs'])


def backwards(apps, schema_editor):
    # Le vecchie chiavi sono mantenute dalla migrazione forward proprio per
    # consentire un rollback senza perdere le preferenze precedenti.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('game', '0026_league_death_bonus'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
