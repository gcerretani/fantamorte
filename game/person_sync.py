"""Sincronizzazione unica di una WikipediaPerson da Wikidata.

Questo modulo è l'UNICO punto in cui lo stato di Wikidata viene applicato a
una persona. Tutti i percorsi che aggiornano i dati — il cron
``check_deaths``, il bottone "Controlla" della pagina admin giocatori,
l'aggiunta in rosa (``_get_or_refresh_person``) — chiamano
:func:`sync_person_from_entity`, così fanno per costruzione le stesse cose
nello stesso ordine. Cambia solo la *strategia di selezione* delle persone
da sincronizzare (batch SPARQL per il cron, click per l'admin, ricerca per
il manager), mai il modo in cui i dati vengono applicati.
"""
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


def sync_person_from_entity(person, entity, *, client, autoconfirm=True):
    """Applica a ``person`` lo stato corrente di Wikidata.

    Ritorna ``(death, death_created)``. Ogni conferma automatica valorizza
    ``confirmed_at`` nella stessa write che imposta ``is_confirmed``: le
    deadline di sostituzione non devono dipendere dal percorso (admin/cron).
    """
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

    if created:
        bonus_types = BonusType.objects.filter(
            is_active=True, detection_method__in=['wikidata', 'age'],
        )
        for bt in client.detect_bonuses(person.wikidata_id, person.claims_cache, bonus_types):
            DeathBonus.objects.get_or_create(
                death=death, bonus_type=bt,
                defaults={'points_awarded': bt.points, 'is_auto_detected': True},
            )
        age = person.get_age_at_death()
        if age is not None:
            for bt in bonus_types.filter(detection_method='age'):
                if client.detect_age_bonus(age, bt):
                    DeathBonus.objects.get_or_create(
                        death=death, bonus_type=bt,
                        defaults={'points_awarded': bt.points, 'is_auto_detected': True},
                    )
    else:
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
        elif death.is_confirmed and death.confirmed_at is None:
            # Repair a legacy inconsistent row only when this sync is allowed
            # to confirm: the current successful Wikidata check is the first
            # trustworthy timestamp we can record without fabricating history.
            if autoconfirm:
                death.confirmed_at = confirmation_time
                update_fields.append('confirmed_at')
        if update_fields:
            death.save(update_fields=update_fields)

    return death, created
