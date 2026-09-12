"""Sincronizzazione unica di una WikipediaPerson da Wikidata."""
import logging
from datetime import date as date_cls

from django.utils import timezone

from .models import BonusType, Death, DeathBonus
from .scoring import invalidate_person_bonus_caches

logger = logging.getLogger(__name__)

ENTITY_FIELDS = (
    'name_it', 'name_en', 'description_it', 'birth_date', 'birth_year',
    'death_date', 'death_year', 'image_url', 'occupation', 'nationality',
    'wikipedia_url_it',
)


def _reconcile_auto_bonuses_for_method(death, method, desired_types):
    desired = {bt.pk: bt for bt in desired_types}
    auto_rows = DeathBonus.objects.filter(
        death=death,
        is_auto_detected=True,
        bonus_type__detection_method=method,
    )
    auto_rows.exclude(bonus_type_id__in=list(desired)).delete()

    for bt in desired.values():
        existing = DeathBonus.objects.filter(death=death, bonus_type=bt).first()
        points = bt.compute_points(age=death.death_age)
        if existing is None:
            DeathBonus.objects.create(
                death=death,
                bonus_type=bt,
                points_awarded=points,
                is_auto_detected=True,
            )
        elif existing.is_auto_detected and existing.points_awarded != points:
            existing.points_awarded = points
            existing.save(update_fields=['points_awarded'])


def reconcile_automatic_bonuses(death, person, client):
    wikidata_types = BonusType.objects.filter(
        is_active=True,
        detection_method=BonusType.DETECTION_WIKIDATA,
    )
    try:
        desired_wikidata = client.detect_bonuses(
            person.wikidata_id,
            person.claims_cache,
            wikidata_types,
        )
    except Exception:
        logger.warning(
            'Riconciliazione bonus Wikidata fallita per %s: mantengo lo stato precedente',
            person.wikidata_id,
            exc_info=True,
        )
    else:
        _reconcile_auto_bonuses_for_method(
            death, BonusType.DETECTION_WIKIDATA, desired_wikidata,
        )

    age = person.get_age_at_death()
    if age is None:
        return
    age_types = BonusType.objects.filter(
        is_active=True,
        detection_method=BonusType.DETECTION_AGE,
    )
    desired_age = [bt for bt in age_types if client.detect_age_bonus(age, bt)]
    _reconcile_auto_bonuses_for_method(
        death, BonusType.DETECTION_AGE, desired_age,
    )


def sync_person_from_entity(person, entity, *, client, autoconfirm=True, force=False):
    """Apply one successfully fetched Wikidata entity and derived state.

    ``data_frozen`` is enforced here, at the single write boundary, instead of
    relying on every caller to remember a guard.  ``force=True`` is the
    explicit maintenance override used by ``check_deaths --force``.
    """
    if person.pk and person.data_frozen and not force:
        return getattr(person, 'death', None), False

    for field in ENTITY_FIELDS:
        new_value = entity.get(field)
        if new_value is None:
            continue
        setattr(person, field, new_value)
    person.is_dead = bool(person.death_date or person.death_year)
    person.claims_cache = entity.get('claims_cache', {})
    person.last_checked = timezone.now()
    person.save()
    invalidate_person_bonus_caches(person)

    if not person.is_dead:
        return None, False

    year_for_death = (person.death_date or date_cls(person.death_year, 1, 1)).year
    confirmation_time = timezone.now() if autoconfirm else None
    death, created = Death.objects.get_or_create(
        person=person,
        defaults={
            'death_date': person.death_date or date_cls(year_for_death, 12, 31),
            'death_age': person.get_age_at_death(),
            'source': Death.SOURCE_WIKIDATA,
            'is_confirmed': autoconfirm,
            'confirmed_at': confirmation_time,
        },
    )

    if not created:
        expected_date = person.death_date or date_cls(year_for_death, 12, 31)
        expected_age = person.get_age_at_death()
        update_fields = []
        if death.death_date != expected_date:
            death.death_date = expected_date
            update_fields.append('death_date')
        if expected_age is not None and death.death_age != expected_age:
            death.death_age = expected_age
            update_fields.append('death_age')
        if autoconfirm and not death.is_confirmed:
            death.is_confirmed = True
            death.confirmed_at = confirmation_time
            update_fields.extend(['is_confirmed', 'confirmed_at'])
        elif autoconfirm and death.is_confirmed and death.confirmed_at is None:
            death.confirmed_at = confirmation_time
            update_fields.append('confirmed_at')
        if update_fields:
            death.save(update_fields=update_fields)

    reconcile_automatic_bonuses(death, person, client)
    return death, created
