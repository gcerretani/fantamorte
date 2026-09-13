"""Central policy for league phase boundaries in the configured local timezone.

The League model historically used ``timezone.now().date()``, which takes the
UTC calendar date when USE_TZ is enabled.  Fantamorte's rules are expressed in
local dates (Europe/Rome in production), so all date-only phase decisions must
use ``timezone.localdate()`` instead.

The functions are installed onto ``League`` at app startup to keep one runtime
source of truth without duplicating phase logic across views/commands.
"""

from django.utils import timezone

from .models import League


def local_today():
    return timezone.localdate()


def is_registration_open(league):
    today = local_today()
    return league.registration_opens <= today <= league.registration_closes


def has_started(league):
    return local_today() >= league.start_date


def is_finished(league):
    # end_date itself is still an active game day; the league ends on the next
    # local calendar day.
    return local_today() > league.end_date


def install():
    League.is_registration_open = is_registration_open
    League.has_started = has_started
    League.is_finished = is_finished
