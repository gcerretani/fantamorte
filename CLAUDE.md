# CLAUDE.md

Guida operativa per Claude Code (e qualsiasi altro agente / sviluppatore)
che debba lavorare su questo progetto. Scritto per essere letto in 5 minuti
prima di mettere mano al codice.

## Cos'è Fantamorte

Web app Django per giocare al "fantacalcio dei decessi": ogni manager
compone una squadra di personaggi pubblici e prende punti quando uno di
loro muore durante il periodo di gioco. I dati provengono da
Wikidata/Wikipedia.

Tutti i contenuti sono **privati**: gli utenti anonimi vedono solo il
login/registrazione. Il gioco è organizzato in **leghe** indipendenti, ognuna
con regole, calendario e bonus configurabili.

## Stack

- **Django 4.2+** (testato fino a 5.2) su Python 3.11
- **MariaDB 11** in produzione, SQLite in sviluppo (via `DATABASE_URL`)
- **Bootstrap 5.3** + vanilla JS per il frontend (server-rendered, no SPA)
- **django-allauth** per auth + OAuth (Google, GitHub)
- **pywebpush** per Web Push (VAPID)
- **Wikidata SPARQL** + Wikipedia API (it) per dati biografici
- **Docker / Docker Compose** + Gunicorn per il deploy

## Struttura

```
fantamorte/
├── fantamorte_project/      # Django project (settings, urls root, wsgi)
├── game/                    # App principale: tutta la logica di gioco
│   ├── models.py            # Tutti i modelli del dominio
│   ├── views.py             # CBV organizzate per area (dashboard, league, team, ...)
│   ├── urls.py              # URL della app
│   ├── admin.py             # Django admin
│   ├── scoring.py           # Calcolo punteggi (sorgente di verità: la League)
│   ├── push.py              # Web Push (VAPID + broadcast)
│   ├── signals.py           # Hook su Death.is_confirmed → push
│   ├── middleware.py        # LoginRequiredEverywhereMiddleware
│   ├── context_processors.py
│   ├── tests.py             # Test di scoring (suite completa ~470 righe)
│   ├── management/commands/ # check_deaths, mark_originals, award_first_last_death, generate_vapid_keys
│   └── migrations/
├── wikidata_api/            # Client SPARQL/Wikipedia (puro utility, niente modelli)
│   ├── client.py            # WikidataClient: search, entity, summary, SPARQL, bonus detection
│   └── sparql.py            # Template query SPARQL (DEATH_CHECK_QUERY, ecc.)
├── templates/
│   ├── base.html            # Layout, navbar offcanvas, modal persona, dark mode
│   ├── account/             # Override allauth (login/signup) con stile Bootstrap
│   └── game/                # Tutti i template della app (+ sw.js renderizzato)
├── static/
│   ├── css/fantamorte.css   # Tema custom + dark mode + componenti
│   ├── js/fantamorte.js     # SW reg, push, install prompt, modal persona, countdown
│   └── pwa/                 # icone manifest + apple-touch + svg
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh            # Migrations + gunicorn
└── .env.example             # Tutti i parametri runtime
```

## Modelli (mappa rapida)

**Sorgente di verità delle regole = `League`.** Tutto il resto ne dipende.

```
User ─┬─ owns ──────────► League ◄── memberships ── LeagueMembership ──► User
      │                     │
      └─ teams ────► Team ──┘
                      │
                      └─ members ──► TeamMember ──► WikipediaPerson ──► Death ─┬─► DeathBonus ──► BonusType
                                                                                │
                                                                                └─► Season (legacy: indicizza per anno)

LeagueBonus = through M2M (League ↔ BonusType) con override punti / formula
```

- **`League`** ha `start_date`, `end_date`, `registration_opens/closes`,
  `base_points`, `captain_multiplier`, `jolly_multiplier`,
  `max_captains`, `max_non_captains`, `jolly_enabled`,
  `substitution_deadline_days`, `visibility` (public/private), `invite_code`,
  `search_wikipedia_langs` (CSV di wiki, es. `itwiki,enwiki`; vuoto = tutta Wikidata).
- **`LeagueMembership.role`** ∈ `owner|admin|member`.
- **`Team`** ha FK a `League` (vincolo unique `(manager, league)` →
  un utente ha **una squadra per lega**). Ha anche `jolly_month` (mese del
  jolly, intero 1-12) e `is_locked` (squadra bloccata: nessuna modifica).
  Mantiene un FK opzionale a `Season` solo per retro-compatibilità.
- **`TeamMember.is_original`** flag che abilita il bonus "giocata originale".
  Calcolato a inizio stagione dal command `mark_originals`. Il campo
  `replaced_by` crea una catena per tracciare le sostituzioni (solo
  l'ultimo membro senza `replaced_by` è attivo).
- **`WikipediaPerson`** ha cache di Wikidata (`claims_cache` JSON) + biografia
  Wikipedia (`summary_it` con `summary_fetched_at` per scadenza 30 giorni).
  Il flag `data_frozen=True` esclude la persona dai check automatici
  (utile se i dati Wikidata sono errati/incompleti); ignorato solo con
  `check_deaths --force`.
- **`BonusType`** può avere `points` fissi oppure `points_formula` dinamica
  (es. `3*(60-age)`); l'eval è whitelistato (`age`, `max`, `min` + operatori
  aritmetici). Il `detection_method` può essere
  `manual|wikidata|age|original|first_death|last_death`.
- **`Death`** ha `is_confirmed` (flag che fa scattare i punti e il push).
  La transizione `False → True` viene tracciata da `_was_confirmed` nel
  pre-save signal.
- **`Season`** è ancora usata da `Death` come "indice per anno"
  (richiesto da `check_deaths` per il filtro SPARQL). **Non** detta
  più le regole di gioco — quelle stanno in `League`.
- **`SiteSettings`** è un singleton (via Django admin) per configurazione
  globale, ad es. `wikidata_check_interval_hours`.
- **`UserProfile`** tiene le preferenze per-utente: `push_enabled`,
  `email_notifications`, `dark_mode`. Creato automaticamente al signup
  via signal.
- **`PushSubscription`** registra endpoint VAPID per-utente con
  `last_used_at` e `auth`/`p256dh` keys.

## Auth e privacy

- `LoginRequiredEverywhereMiddleware` (in `game/middleware.py`)
  blocca chiunque non sia loggato. Pubblici solo:
  - `/accounts/*` (login, signup, password reset, social)
  - `/static/*`, `/media/*`
  - `/manifest.webmanifest`, `/sw.js`, `/offline/`, `/favicon.ico`, `/robots.txt`
- `django-allauth` gestisce login + signup + reset + provider social.
  Le viste hanno il prefisso `account_` (`account_login`, `account_logout`,
  `account_signup`, `account_reset_password`). **Non** usare i nomi vecchi
  `login`/`logout` di `django.contrib.auth.urls`: l'include è stato rimosso.
- Provider social (Google/GitHub) si attivano popolando le env
  `GOOGLE_OAUTH_CLIENT_ID/SECRET` e `GITHUB_OAUTH_CLIENT_ID/SECRET`. Se vuote
  i pulsanti spariscono automaticamente.

## PWA + Push

- **Manifest** servito da `/manifest.webmanifest` (rendering JSON, view in `views.py`).
- **Service worker** servito da `/sw.js` (template Django, niente static).
  Cache offline: network-first per HTML, cache-first per asset; gestisce push.
  Il `cache_version` nel nome della cache è parametrico nel template Django
  per evitare stale assets.
- **VAPID**: chiavi in env (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
  `VAPID_CLAIM_EMAIL`). Genera con `python manage.py generate_vapid_keys`.
  Senza VAPID, i tentativi di push sono no-op (non crashano).
- **Trigger push**: signal post_save su `Death`. Quando passa
  `is_confirmed=False → True`, `game.push.broadcast_death_notification`
  manda a tutti i `LeagueMembership` delle leghe il cui range contiene
  `death.death_date`. Notifica "urgent" se la persona è nella squadra
  dell'utente. Le sottoscrizioni 404/410 vengono cancellate automaticamente.

## Scoring (regole di calcolo)

Implementato in `game/scoring.py`. La **League** è la sorgente di verità:

1. Punti base = `league.base_points` (default 50).
2. Bonus = somma di `LeagueBonus.compute_points(age)` per ogni `DeathBonus`.
   Se un bonus non è in `LeagueBonus` per quella lega, **non viene contato**.
3. Se `member.is_original`, somma anche i bonus con
   `detection_method='original'` attivi nella lega.
4. Moltiplicatore = `captain_multiplier` (se capitano) × `jolly_multiplier`
   (se mese jolly) — moltiplicano tra loro (es. entrambi attivi = 4×).
5. Le morti considerate sono solo quelle con `is_confirmed=True` e
   `start_date ≤ death_date ≤ end_date` della lega.

API pubblica:
- `compute_team_total_score(team)` → int
- `compute_team_death_details(team)` → lista di dict con base, bonuses, multipliers
- `compute_team_points_for_death(team, death)` → int
- `compute_league_rankings(league)` → lista di dict ordinata per punteggio desc

## Wikidata API client

In `wikidata_api/client.py`. Nessun modello Django — è utility pura.

- `search_by_italian_name(name, require_wikis=None)`: `wbsearchentities` + query SPARQL
  (`HUMAN_SEARCH_QUERY`) per filtrare solo umani (P31=Q5) e, opzionalmente, solo persone
  con una pagina nelle wiki indicate (es. `['itwiki','enwiki']`). Più preciso del vecchio
  flusso Wikipedia-search → pageprops.
- `get_entity(qid)`: fetch completo (labels, claims, immagine Commons, occupazione, nazionalità, URL Wikipedia)
- `get_summary(wiki_title)`: intro da Wikipedia italiana (cacheata 30 giorni)
- `check_deaths_batch(qids, year)`: query SPARQL batch per morti in un dato anno
- `detect_bonuses(qid, claims_cache, bonus_types)`: verifica proprietà Wikidata per i bonus
- `detect_age_bonus(age, bonus_type)`: valuta formula età con whitelist

Le query SPARQL sono template in `wikidata_api/sparql.py`:
`DEATH_CHECK_QUERY`, `HUMAN_SEARCH_QUERY` (nuova).

Config in `settings.py`: `WIKIDATA_USER_AGENT` (default `'Fantamorte/1.0'`),
`WIKIDATA_REQUEST_DELAY` (0.5 s di rate limit tra richieste; azzerato per ricerche
interattive).

## URL principali (mappa)

```
/                               home (dashboard utente)
/leghe/                         lista leghe pubbliche
/leghe/nuova/                   crea lega
/leghe/<slug>/                  detail (top 3 + recent deaths + regole + iscrizione)
/leghe/<slug>/admin/            pannello admin (regole, bonus, membri, invito)
/leghe/<slug>/classifica/       classifica completa
/leghe/<slug>/decessi/          timeline decessi
/leghe/<slug>/giocatori/        refresh Wikidata giocatori della lega (admin)
/leghe/<slug>/squadra/nuova/    crea la mia squadra in questa lega

/squadra/<pk>/                  dettaglio squadra
/squadra/<pk>/modifica/         edit squadra (rosa, capitano, jolly)
/squadra/<pk>/aggiungi/         POST AJAX: aggiunge persona (Wikidata)
/squadra/<pk>/sostituisci/<member_pk>/    flusso sostituzione

/persona/<pk>/                  pagina dettaglio (con bio Wikipedia)
/morte/<pk>/                    dettaglio decesso con bonus e squadre coinvolte
/api/persona/<pk>/              JSON per il modal "click sul nome"
/api/search-person/             autocomplete Wikidata (accetta ?q=&league=<slug> per filtrare per lingua)
/api/leghe/<slug>/wikidata-diff/    JSON POST: diff campi Wikidata vs DB (admin)
/api/leghe/<slug>/wikidata-apply/   JSON POST: applica campi selezionati (admin)

/profilo/                       preferenze utente (push/email/dark mode)
/regolamento/                   regolamento generico

/api/push/{subscribe,unsubscribe,test}/

/manifest.webmanifest, /sw.js, /offline/    PWA
/accounts/...                   allauth (login, signup, password reset, social)
/admin/                         Django admin
```

## Frontend conventions

- **Bootstrap 5.3** è caricato da CDN da `base.html`. Niente bundler.
- Stili custom in `static/css/fantamorte.css` (dark mode con
  `data-theme="dark"` su `<html>`).
- JS custom in `static/js/fantamorte.js`: tema, install prompt, push,
  modal persona, countdown sostituzioni, toast. Tutto attaccato a
  `window.fm*` (`fmShowPerson`, `fmEnablePush`, `fmToast`, ...).
- La ricerca persona (`/api/search-person/`) usa debounce 600 ms e
  `AbortController` per annullare richieste obsolete; i risultati sono
  cachati 5 min lato Django. Errori mostrati inline, niente `alert()`.
- Per aprire il **modal dettagli persona** ovunque, basta un
  `<a href="#" data-fm-person-pk="{{ person.pk }}">…</a>` — il listener
  globale fa il resto. Da preferire al link a `/persona/<pk>/` quando si
  resta nel flusso di una pagina.
- Per il **countdown** della deadline sostituzione, usa
  `<span class="fm-countdown" data-fm-countdown="{{ deadline|date:'U' }}">…</span>`.

## Convenzioni di codice

- I template stanno in `templates/<app>/<page>.html` (non in `<app>/templates/...`).
- I bonus della lega si modificano dal pannello admin `/leghe/<slug>/admin/`,
  **non** dal Django admin (quello è un fallback per superuser).
- Le management commands lavorano per **lega**, non per stagione. Usano
  l'argomento `--league <slug>` o, in mancanza, prendono le leghe in corso.
- Il middleware login-required è la **prima** linea di difesa. Non aggiungere
  endpoint pubblici senza inserirli in `PUBLIC_PATHS` o `PUBLIC_PREFIXES`
  in `game/middleware.py`.
- Le push sono best-effort: il signal cattura ogni eccezione e logga.
- Per il dark mode: il valore `data-theme` viene applicato inline da
  `base.html` prima del rendering per evitare il flash.
- Le view AJAX restituiscono JSON con `status: "ok"|"error"` e codici HTTP
  appropriati (400/403/404).

## Test

La suite di test in `game/tests.py` (~470 righe) copre il calcolo del
punteggio in modo esaustivo:

- `ScoringBaseTestCase`: fixture con lega, stagione, squadra e 3 personaggi
  storici (Berlusconi, Giovanni Paolo II, Fellini)
- `PuntiBaseTest`: punti base, persona non in rosa, morti non confermate
- `BonusFissoTest`: bonus fissi, override lega, override formula
- `BonusFormulaTest`: formule età dinamiche (es. `2*(90-age)`)
- `MoltiplicatoriTest`: capitano, mese jolly, combinazioni, override lega
- `BonusOriginaleTest`: flag `is_original`
- `FiltriDateLeagaTest`: filtro `start_date`/`end_date` con condizioni di
  bordo
- `TotaleEDeathDetailsTest`: aggregati e struttura dei dettagli
- `RankingTest`: ordinamento classifica, pareggi

**Aree non ancora coperte dai test**: view (integrazione), Wikidata client
(API esterna), signal handler, push notifications, admin actions.

Esegui i test con:
```bash
python manage.py test game
```

## Comandi utili

```bash
# Setup locale
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # poi compila i valori
python manage.py migrate
python manage.py createsuperuser

# VAPID per le push
python manage.py generate_vapid_keys

# Sviluppo
python manage.py runserver

# Cron / job periodici (per ogni lega in corso)
python manage.py check_deaths              # rileva morti via Wikidata
python manage.py check_deaths --dry-run    # senza scrivere
python manage.py check_deaths --force      # ignora data_frozen sulle persone
python manage.py mark_originals            # a inizio stagione di una lega
python manage.py award_first_last_death --league <slug> --first   # primo decesso
python manage.py award_first_last_death --league <slug> --last    # fine stagione
python manage.py send_substitution_reminders             # reminder push/email per sostituzioni (T-3, T-1)
python manage.py send_substitution_reminders --dry-run   # solo log, niente invio

# Docker
docker compose up -d
docker compose exec web python manage.py migrate
```

## Quando aggiungi una feature

1. Modelli → `game/models.py`. Crea sempre la migration:
   `python manage.py makemigrations`.
2. Logica di punteggio → `game/scoring.py` (e prendi i parametri dalla `League`).
3. View → `game/views.py`. Usa `LoginRequiredMixin` per sicurezza ulteriore
   (il middleware già copre, ma raddoppiare aiuta a leggere il codice).
4. URL → `game/urls.py`. Le rotte pubbliche **devono** entrare in
   `PUBLIC_PATHS`/`PUBLIC_PREFIXES` (`game/middleware.py`).
5. Template → `templates/game/<page>.html`. Estendi `base.html`.
6. Static → `static/css|js|...`. Da CDN solo Bootstrap.
7. Test → `game/tests.py`. Aggiungi casi di test per la logica di punteggio
   o qualsiasi logica di business non banale.

## Aree migliorabili / TODO suggeriti

- Inviti via email per leghe private (oggi solo codice condiviso)
- Statistiche cross-lega (storico per utente, leaderboard "all-time")
- Test di integrazione per le view e per il client Wikidata
- Possibile rimozione completa di `Season` (richiede di rivedere
  `check_deaths` e `Death.season`); valutare quando ci sarà tempo
- Indici DB su `Death.death_date`, `Team.league`, `LeagueMembership.user`
  se le leghe diventano numerose
- API REST con DRF se serve un'app mobile nativa
