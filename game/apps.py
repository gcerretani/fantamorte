from django.apps import AppConfig


class GameConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "game"

    def ready(self):
        # LeagueDeathBonus lives in a small dedicated module to keep the
        # security boundary explicit while remaining part of the game app.
        from . import league_bonus_decisions  # noqa: F401
        from . import signals  # noqa: F401
        from . import league_bonus_signals  # noqa: F401
        from . import league_bonus_admin  # noqa: F401
