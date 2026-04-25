import calendar
from datetime import timedelta
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Season(models.Model):
    year = models.IntegerField(unique=True)
    is_active = models.BooleanField(default=False)
    registration_opens = models.DateField()
    registration_closes = models.DateField()
    substitution_deadline_days = models.PositiveIntegerField(
        default=7,
        help_text='Giorni a disposizione per sostituire un giocatore deceduto, dalla conferma del decesso.'
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-year']

    def __str__(self):
        return str(self.year)

    def is_registration_open(self):
        today = timezone.now().date()
        return self.registration_opens <= today <= self.registration_closes

    def is_running(self):
        from datetime import date
        today = timezone.now().date()
        start = date(self.year, 1, 1)
        end = date(self.year, 12, 31)
        return start <= today <= end


class WikipediaPerson(models.Model):
    wikidata_id = models.CharField(max_length=20, unique=True)
    name_it = models.CharField(max_length=300)
    name_en = models.CharField(max_length=300, blank=True)
    description_it = models.CharField(max_length=500, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    birth_year = models.IntegerField(null=True, blank=True)
    death_date = models.DateField(null=True, blank=True)
    death_year = models.IntegerField(null=True, blank=True)
    is_dead = models.BooleanField(default=False)
    image_url = models.URLField(max_length=500, blank=True)
    occupation = models.CharField(max_length=300, blank=True)
    nationality = models.CharField(max_length=100, blank=True)
    summary_it = models.TextField(blank=True)
    summary_fetched_at = models.DateTimeField(null=True, blank=True)
    claims_cache = models.JSONField(default=dict, blank=True)
    last_checked = models.DateTimeField(null=True, blank=True)
    wikipedia_url_it = models.URLField(blank=True)

    class Meta:
        verbose_name = 'Persona Wikipedia'
        verbose_name_plural = 'Persone Wikipedia'
        ordering = ['name_it']

    def __str__(self):
        return f'{self.name_it} ({self.wikidata_id})'

    def get_age_at_death(self):
        if not self.is_dead:
            return None
        death = self.death_date
        birth = self.birth_date
        if death and birth:
            age = death.year - birth.year
            if (death.month, death.day) < (birth.month, birth.day):
                age -= 1
            return age
        if self.death_year and self.birth_year:
            return self.death_year - self.birth_year
        return None


class BonusType(models.Model):
    DETECTION_MANUAL = 'manual'
    DETECTION_WIKIDATA = 'wikidata'
    DETECTION_AGE = 'age'
    DETECTION_ORIGINAL = 'original'
    DETECTION_FIRST_DEATH = 'first_death'
    DETECTION_LAST_DEATH = 'last_death'
    DETECTION_CHOICES = [
        (DETECTION_MANUAL, 'Manuale'),
        (DETECTION_WIKIDATA, 'Proprietà Wikidata'),
        (DETECTION_AGE, 'Formula età'),
        (DETECTION_ORIGINAL, 'Giocata originale'),
        (DETECTION_FIRST_DEATH, 'Primo decesso della stagione'),
        (DETECTION_LAST_DEATH, 'Ultimo decesso della stagione'),
    ]

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    points = models.IntegerField(
        help_text='Punti fissi. Ignorato se points_formula è valorizzato.'
    )
    points_formula = models.CharField(
        max_length=200, blank=True,
        help_text='Formula per punti dinamici, può usare la variabile `age` (es. "3*(60-age)").'
    )
    detection_method = models.CharField(
        max_length=20, choices=DETECTION_CHOICES, default=DETECTION_MANUAL
    )
    wikidata_property = models.CharField(max_length=20, blank=True)
    wikidata_value = models.CharField(max_length=20, blank=True)
    age_formula = models.CharField(
        max_length=100, blank=True,
        help_text='Condizione che deve essere vera (es. "age < 60").'
    )
    is_active = models.BooleanField(default=True)
    ordering = models.IntegerField(default=0)

    class Meta:
        ordering = ['ordering', 'name']
        verbose_name = 'Tipo bonus'
        verbose_name_plural = 'Tipi bonus'

    def __str__(self):
        return f'{self.name} (+{self.points})'

    def compute_points(self, age=None):
        """Restituisce i punti di questo bonus per una data età (se applicabile)."""
        formula = (self.points_formula or '').strip()
        if not formula:
            return self.points
        # eval whitelist: solo cifre, operatori e variabile age
        allowed = set('0123456789+-*/(). agemax(),min')
        if not all(c in allowed for c in formula):
            return self.points
        try:
            value = eval(formula, {'__builtins__': {}}, {'age': age or 0, 'max': max, 'min': min})
            return int(value)
        except Exception:
            return self.points


MONTHS_IT = [
    (1, 'Gennaio'), (2, 'Febbraio'), (3, 'Marzo'), (4, 'Aprile'),
    (5, 'Maggio'), (6, 'Giugno'), (7, 'Luglio'), (8, 'Agosto'),
    (9, 'Settembre'), (10, 'Ottobre'), (11, 'Novembre'), (12, 'Dicembre'),
]


class Team(models.Model):
    name = models.CharField(max_length=200)
    manager = models.ForeignKey(User, on_delete=models.CASCADE, related_name='teams')
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='teams')
    jolly_month = models.IntegerField(choices=MONTHS_IT, null=True, blank=True)
    is_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('manager', 'season')]
        ordering = ['name']
        verbose_name = 'Squadra'
        verbose_name_plural = 'Squadre'

    def __str__(self):
        return f'{self.name} ({self.season.year})'

    def get_captain(self):
        return self.members.filter(is_captain=True, replaced_by=None).first()

    def get_active_members(self):
        return self.members.filter(replaced_by=None)

    def get_active_non_captain_count(self):
        return self.members.filter(is_captain=False, replaced_by=None).count()


class TeamMember(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='members')
    person = models.ForeignKey(WikipediaPerson, on_delete=models.PROTECT, related_name='team_members')
    is_captain = models.BooleanField(default=False)
    is_original = models.BooleanField(
        default=False,
        help_text='Giocata originale: la persona è stata scelta solo da questo manager all\'inizio della stagione.'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    replaced_by = models.OneToOneField(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='replaces'
    )

    class Meta:
        verbose_name = 'Membro squadra'
        verbose_name_plural = 'Membri squadra'

    def __str__(self):
        flag = ' [C]' if self.is_captain else ''
        replaced = ' [sostituito]' if self.replaced_by else ''
        return f'{self.person.name_it}{flag}{replaced} → {self.team.name}'

    def is_active(self):
        return self.replaced_by is None

    def get_substitution_deadline(self):
        """Restituisce la deadline (datetime) entro cui questo membro può essere sostituito.

        Si basa sulla data di conferma del decesso e sulla configurazione della stagione.
        Restituisce None se il membro non è ancora morto o non è confermato.
        """
        if not self.person.is_dead:
            return None
        death = getattr(self.person, 'death', None)
        if not death or not death.is_confirmed or not death.confirmed_at:
            return None
        days = self.team.season.substitution_deadline_days or 0
        if days <= 0:
            return None
        return death.confirmed_at + timedelta(days=days)

    def can_be_substituted(self):
        """True se il membro è morto, non già sostituito e la deadline non è scaduta."""
        if not self.is_active() or not self.person.is_dead:
            return False
        deadline = self.get_substitution_deadline()
        if deadline is None:
            return True
        return timezone.now() <= deadline

    def substitution_seconds_remaining(self):
        deadline = self.get_substitution_deadline()
        if deadline is None:
            return None
        delta = deadline - timezone.now()
        return max(int(delta.total_seconds()), 0)


class Death(models.Model):
    SOURCE_WIKIDATA = 'wikidata'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_WIKIDATA, 'Rilevato da Wikidata'),
        (SOURCE_MANUAL, 'Inserito manualmente'),
    ]

    person = models.OneToOneField(
        WikipediaPerson, on_delete=models.CASCADE, related_name='death'
    )
    season = models.ForeignKey(Season, on_delete=models.CASCADE, related_name='deaths')
    death_date = models.DateField()
    death_age = models.IntegerField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default=SOURCE_WIKIDATA)
    notes = models.TextField(blank=True)
    is_confirmed = models.BooleanField(default=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='confirmed_deaths'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['death_date']
        verbose_name = 'Decesso'
        verbose_name_plural = 'Decessi'

    def __str__(self):
        confirmed = ' ✓' if self.is_confirmed else ' (non confermato)'
        return f'{self.person.name_it} † {self.death_date}{confirmed}'


class DeathBonus(models.Model):
    death = models.ForeignKey(Death, on_delete=models.CASCADE, related_name='bonuses')
    bonus_type = models.ForeignKey(BonusType, on_delete=models.PROTECT, related_name='awarded')
    points_awarded = models.IntegerField()
    is_auto_detected = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [('death', 'bonus_type')]
        verbose_name = 'Bonus decesso'
        verbose_name_plural = 'Bonus decesso'

    def __str__(self):
        return f'{self.bonus_type.name} per {self.death.person.name_it}'


class UserProfile(models.Model):
    """Preferenze utente: opt-in/out notifiche, tema, ecc."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    push_notifications_enabled = models.BooleanField(
        default=True,
        help_text='Ricevi notifiche push quando un decesso viene confermato.'
    )
    email_notifications_enabled = models.BooleanField(
        default=True,
        help_text='Ricevi email quando un decesso viene confermato o un tuo membro è morto.'
    )
    dark_mode = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Profilo utente'
        verbose_name_plural = 'Profili utente'

    def __str__(self):
        return f'Profilo di {self.user.username}'


class PushSubscription(models.Model):
    """Endpoint Web Push (VAPID) registrato dal browser di un utente."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Iscrizione push'
        verbose_name_plural = 'Iscrizioni push'

    def __str__(self):
        return f'{self.user.username} ({self.endpoint[:50]}…)'

    def to_dict(self):
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }
