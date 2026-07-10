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

# Campi anagrafici applicati 1:1 dall'entità Wikidata. Un valore None
# nell'entità significa "non determinabile" (es. timeout label lookup):
# non sovrascrive mai un valore esistente.
ENTITY_FIELDS = (
    'name_it', 'name_en', 'description_it', 'birth_date', 'birth_year',
    'death_date', 'death_year', 'image_url', 'occupation', 'nationality',
    'wikipedia_url_it',
)


def sync_person_from_entity(person, entity, *, client, autoconfirm=True):
    """Applica a ``person`` lo stato corrente di Wikidata (entità già scaricata).

    Nell'ordine:

    1. aggiorna i campi anagrafici (:data:`ENTITY_FIELDS`, con guardia sui
       None) e ricalcola ``is_dead``;
    2. aggiorna ``claims_cache`` e invalida le cache bonus derivate
       (``fm_potential``, ``wd_bonus``);
    3. aggiorna ``last_checked``;
    4. se la persona risulta deceduta, registra la :class:`Death` con
       auto-rilevazione dei bonus (wikidata + età) e conferma secondo
       ``autoconfirm`` — o promuove a confermata una Death esistente non
       confermata. La conferma fa scattare punti e notifiche via signal.

    ``person`` può essere anche un'istanza non ancora salvata (aggiunta in
    rosa di una persona nuova). Ritorna ``(death, death_created)``:
    ``(None, False)`` se la persona è viva.
    """
    for field in ENTITY_FIELDS:
        new_value = entity.get(field)
        if new_value is None:
            # None = dato non determinabile da Wikidata (es. timeout label
            # lookup) oppure assente: mai sovrascrivere il valore esistente
            # (e mai scrivere None nei CharField NOT NULL).
            continue
        setattr(person, field, new_value)
    person.is_dead = bool(person.death_date or person.death_year)
    person.claims_cache = entity.get('claims_cache', {})
    person.last_checked = timezone.now()
    person.save()
    # I claim sono appena stati rinfrescati: un esito negativo cachato del
    # check gerarchico (wd_bonus, 7 giorni) non deve sopravvivere e far
    # perdere bonus alla detection qui sotto.
    invalidate_person_bonus_caches(person)

    if not person.is_dead:
        return None, False

    year_for_death = (person.death_date or date_cls(person.death_year, 1, 1)).year
    death, created = Death.objects.get_or_create(
        person=person,
        defaults={
            'death_date': person.death_date or date_cls(year_for_death, 12, 31),
            'death_age': person.get_age_at_death(),
            'source': Death.SOURCE_WIKIDATA,
            # Il dato arriva da Wikidata con data valida: si conferma subito
            # (punti + notifiche via signal). Revocabile da admin; data_frozen
            # sulla persona la esclude dai check automatici successivi.
            'is_confirmed': autoconfirm,
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
    elif autoconfirm and not death.is_confirmed:
        # Decesso già registrato ma mai confermato: promuovilo (la
        # transizione False→True fa scattare punti e notifiche).
        death.is_confirmed = True
        death.save()

    return death, created
