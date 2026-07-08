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
- **Redis 7** come cache condivisa in produzione (`REDIS_URL`, impostata dal
  compose su web **e** scheduler: le invalidazioni delle classifiche devono
  raggiungere i worker web). Senza `REDIS_URL` → LocMemCache per-processo
  (sviluppo/test).
- **Bootstrap 5.3** (CDN con SRI) + vanilla JS per il frontend
  (server-rendered, no SPA)
- **django-allauth** per auth + OAuth (Google, GitHub); form con classi
  Bootstrap applicate server-side via `ACCOUNT_FORMS` → `game/forms.py`
- **pywebpush** per Web Push (VAPID)
- **Email transazionali** implementate in `game/email.py` (template testo+HTML
  in `templates/email/`): notifica decesso e reminder sostituzione
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
│   ├── forms.py             # Form allauth con classi Bootstrap (ACCOUNT_FORMS)
│   ├── scoring.py           # Calcolo punteggi (sorgente di verità: la League)
│   ├── push.py              # Web Push (VAPID + broadcast)
│   ├── email.py             # Email transazionali (decesso, reminder sostituzione)
│   ├── signals.py           # Hook su Death.is_confirmed → push + email
│   ├── middleware.py        # LoginRequiredEverywhereMiddleware
│   ├── context_processors.py
│   ├── tests.py             # Test di scoring + email + reminder + tema
│   ├── tests_commands.py    # Test management command (check_deaths)
│   ├── tests_middleware.py  # Test LoginRequiredEverywhereMiddleware
│   ├── tests_views.py       # Test permessi/integrazione view
│   ├── management/commands/ # check_deaths, mark_originals, send_substitution_reminders, generate_vapid_keys
│   └── migrations/
├── wikidata_api/            # Client SPARQL/Wikipedia (puro utility, niente modelli)
│   ├── client.py            # WikidataClient: search, entity, summary, SPARQL, bonus detection
│   ├── sparql.py            # Template query SPARQL (DEATH_CHECK_QUERY, ecc.)
│   └── tests.py             # Test del client (mockando le chiamate HTTP)
├── templates/
│   ├── base.html            # Layout, navbar offcanvas, modal persona, dark mode
│   ├── account/             # Override allauth (login/signup) con stile Bootstrap
│   ├── email/               # Template email transazionali (txt + html)
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
                      └─ members ──► TeamMember ──► WikipediaPerson ──► Death ──► DeathBonus ──► BonusType

LeagueBonus = through M2M (League ↔ BonusType) con override punti / formula
```

- **`League`** ha `start_date`, `end_date`, `registration_opens/closes`,
  `base_points`, `captain_multiplier`, `jolly_multiplier`,
  `max_captains`, `max_non_captains`, `max_total_age` (somma massima delle
  età dei membri attivi di una squadra, 0 = nessun limite; enforced in
  AddPersonView e SubstituteMemberView), `jolly_enabled`,
  `substitution_deadline_days`, `visibility` (public/private), `invite_code`,
  `search_wikipedia_langs` (CSV di wiki, es. `itwiki,enwiki`; vuoto = tutta Wikidata).
- **`LeagueMembership.role`** ∈ `owner|admin|member`.
- **`Team`** ha FK a `League` (vincolo unique `(manager, league)` →
  un utente ha **una squadra per lega**). Ha anche `jolly_month` (mese del
  jolly, intero 1-12) e `is_locked` (squadra bloccata: il manager non può
  più modificare la rosa — enforced in `_can_edit_team`; le sostituzioni
  in stagione restano governate da `can_be_substituted()`).
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
  `manual|wikidata|age|original|first_death|last_death`. Il campo `league`
  (nullable) distingue i bonus **di sistema** (NULL, proposti a tutte le
  leghe) dai bonus **personalizzati di lega**, creabili dal pannello admin
  della lega indicando una coppia proprietà/valore Wikidata (es. `P166=Q41254`
  per il Grammy). La detection `wikidata` prova prima il match esatto sui
  claim in cache, poi un match gerarchico via SPARQL che segue
  `P31/P279/P361` (così `P166=Q38104` "Nobel per la fisica" soddisfa il
  bonus generico `Q7191` "Premio Nobel"). I P/Q id sono validati con regex
  prima di finire nella query.
- **`Death`** ha `is_confirmed` (flag che fa scattare i punti, il push e le
  email). La transizione `False → True` viene tracciata da `_was_confirmed`
  nel pre-save signal. `check_deaths` **auto-conferma**: un decesso rilevato
  su Wikidata con una data valida nasce già `is_confirmed=True` (usa
  `--no-autoconfirm` per crearlo non confermato). Dal Django admin l'azione
  "Revoca conferma" rimette `is_confirmed=False` (via `update()`, quindi
  senza notifiche); per escludere definitivamente la persona dai check
  automatici successivi occorre anche impostare `data_frozen=True` sulla
  `WikipediaPerson`.
- **`SiteSettings`** è un singleton (via Django admin) per configurazione
  globale, ad es. `wikidata_check_interval_hours`.
- **`UserProfile`** tiene le preferenze per-utente: `push_notifications_enabled`,
  `email_notifications_enabled`, `theme_preference` (`auto|light|dark`).
  Creato automaticamente al signup via signal.
- **`PushSubscription`** registra endpoint VAPID per-utente con
  `last_used_at` e `auth`/`p256dh` keys.
- **`SubstitutionReminder`** traccia i reminder di scadenza sostituzione già
  inviati (unique per `team_member` + `threshold_days`), per evitare invii
  duplicati. Usato da `send_substitution_reminders` per le soglie T-3 e T-1
  giorni prima della `substitution_deadline_days`.

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
  `GOOGLE_OAUTH_CLIENT_ID/SECRET` e `GITHUB_OAUTH_CLIENT_ID/SECRET`, **oppure**
  creando un `SocialApp` dal Django admin
  (`/admin/socialaccount/socialapp/`) lasciando le env vuote — se le env
  sono vuote `SOCIALACCOUNT_PROVIDERS` in `settings.py` non registra
  nessuna app per quel provider, evitando conflitti con quella creata da
  admin (allauth unirebbe le due app e romperebbe il login con
  `MultipleObjectsReturned`). Non mescolare i due canali per lo stesso
  provider. Con entrambi i canali vuoti/assenti i pulsanti spariscono
  automaticamente (`{% get_providers %}` in `templates/account/login.html`
  e `signup.html` mostra solo i provider effettivamente configurati).

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
4. I bonus `first_death`/`last_death` sono **calcolati dinamicamente per
   lega** (primo/ultimo decesso confermato nel periodo della lega; l'ultimo
   solo a lega conclusa). Non esistono righe `DeathBonus` per questi tipi:
   le leghe condividono solo il database degli eventi, nessuna correlazione.
5. Moltiplicatore = `captain_multiplier` (se capitano) × `jolly_multiplier`
   (se mese jolly) — moltiplicano tra loro (es. entrambi attivi = 4×).
6. Le morti considerate sono solo quelle con `is_confirmed=True` e
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

Note di efficienza (importanti se tocchi il client):
- La `requests.Session` è **condivisa a livello di modulo** (riuso
  connessioni/TLS) con retry automatico su errori di connessione e
  502/503/504. Nei test usa `_reset_session_for_tests()`.
- Il rate limit (`_throttle`) si applica solo **tra richieste consecutive**,
  mai prima della prima: le viste interattive non pagano lo sleep.
- Timeout per-istanza (`client.timeout`, `client.sparql_timeout`, default
  15/30 s): `PersonSearchView` li abbassa a 5/8 s per fallire in fretta.
- `get_entity` risolve occupazione+cittadinanza con **una** `wbgetentities`.
- I check gerarchici dei bonus (ASK con property path) sono **cachati 7
  giorni** nella cache Django (`wd_bonus:*`).
- Il summary Wikipedia è **lazy**: `/api/persona/<pk>/` risponde solo con i
  dati in DB + flag `summary_stale`; il refresh sincrono sta in
  `/api/persona/<pk>/summary/`, chiamato dal client dopo il render del modal.
- Gli endpoint bulk diff/apply accettano **max 10 persone per richiesta**
  (`MAX_DIFF_BATCH`): il fan-out lo fa il browser a blocchi con concorrenza
  2 (vedi `league_players_refresh.html`), mai una singola richiesta lunga.

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
/squadra/<pk>/what-if/          simulatore punti (capitano/jolly)

/persona/<pk>/                  pagina dettaglio (con bio Wikipedia)
/morte/<pk>/                    dettaglio decesso con bonus e squadre coinvolte
/api/persona/<pk>/              JSON per il modal (solo dati in DB + summary_stale)
/api/persona/<pk>/summary/      refresh sincrono del summary Wikipedia (lazy dal modal)
/api/search-person/             autocomplete Wikidata (accetta ?q=&league=<slug> per filtrare per lingua)
/api/leghe/<slug>/wikidata-diff/    JSON POST: diff campi Wikidata vs DB (admin, max 10 persone)
/api/leghe/<slug>/wikidata-apply/   JSON POST: applica campi selezionati (admin, max 10 persone)

/profilo/                       preferenze utente (push/email/dark mode)
/statistiche/                   statistiche cross-lega (storico + leaderboard all-time)
/regolamento/                   regolamento generico
/healthz/                       healthcheck (pubblico, verifica anche il DB)

/api/push/{subscribe,unsubscribe,test}/

/manifest.webmanifest, /sw.js, /offline/    PWA
/accounts/...                   allauth (login, signup, password reset, social)
/admin/                         Django admin
```

## Frontend conventions

- **Bootstrap 5.3** è caricato da CDN da `base.html` con hash SRI
  (aggiorna gli hash quando cambi versione; ricalcolo dal pacchetto npm:
  `openssl dgst -sha384 -binary | openssl base64 -A`). Niente bundler.
  Gli URL versionati vanno tenuti allineati anche nel precache di
  `templates/game/sw.js`.
- **Dark mode nativo Bootstrap**: lo script anti-FOUC in `base.html` (e il
  toggle in `fantamorte.js`) scrivono `data-bs-theme="light|dark"` su
  `<html>`; la preferenza tri-state (`auto|light|dark`) sta in
  `data-theme-pref` + localStorage. In `fantamorte.css` restano solo poche
  regole custom basate sulle variabili `--bs-*`: **non** aggiungere override
  a mano per componenti Bootstrap in dark mode.
- **Convenzione bottoni**: `btn-primary` per l'azione affermativa/primaria
  (Salva, Aggiungi, Iscriviti, Conferma, Crea…), `btn-outline-secondary`
  per azioni secondarie e navigazione, `btn-danger`/`btn-outline-danger`
  solo per azioni distruttive. Mai `btn-dark`/`btn-outline-dark`/
  `btn-warning`/`btn-success` (non si adattano al dark mode nativo).
- **Convenzione badge**: sempre `text-bg-*` (mai `bg-*` nudo):
  `danger`=morte, `success`=vivo/attivo/confermato, `primary`=capitano,
  `info`=meccaniche di gioco (jolly, originale, personalizzato),
  `warning`=stati di attenzione (non confermato), `secondary`=meta
  (ruoli, punteggi, stati neutri).
- JS custom in `static/js/fantamorte.js`: tema, install prompt, push,
  modal persona, countdown sostituzioni, toast (via `bootstrap.Toast`),
  ricerca persona. Tutto attaccato a `window.fm*` (`fmShowPerson`,
  `fmEnablePush`, `fmToast`, `fmPersonSearch`, `fmInitCountdowns`, ...).
- La **ricerca persona** è un componente condiviso: partial
  `templates/game/_person_search.html` (elementi marcati `data-fm-role`)
  + `fmPersonSearch(rootEl, {onSelect})` (debounce 600 ms,
  `AbortController`, errori inline; metodo `reset()`). Risultati cachati
  5 min lato Django. Usato da team_edit e substitute_member: non duplicare
  la logica nei template.
- In team_edit l'aggiunta persona **non ricarica la pagina**: rifetch
  dell'HTML e replace di `#fmRosterHeader` + `#fmRosterRegion`, poi
  `fmInitCountdowns(region)` e `fmToast`. Mantieni gli id se ristrutturi
  il template.
- Per aprire il **modal dettagli persona** ovunque, basta un
  `<a href="#" data-fm-person-pk="{{ person.pk }}">…</a>` — il listener
  globale fa il resto. Il modal apre con uno skeleton
  (`<template id="fmPersonSkeleton">` in base.html) e carica la biografia
  scaduta in lazy da `/api/persona/<pk>/summary/`.
- Per il **countdown** della deadline sostituzione, usa
  `<span class="fm-countdown" data-fm-countdown="{{ deadline|date:'U' }}">…</span>`
  (initializzato da `fmInitCountdowns`, richiamabile su un sottoalbero dopo
  un replace del DOM).
- Animazioni: nessuna oltre a quelle di Bootstrap; eventuali transizioni
  custom vanno dentro `@media (prefers-reduced-motion: no-preference)`.

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
- Per il dark mode: il valore `data-bs-theme` viene applicato inline da
  `base.html` prima del rendering per evitare il flash.
- Le view AJAX restituiscono JSON con `status: "ok"|"error"` e codici HTTP
  appropriati (400/403/404).
- Le chiamate a Wikidata/Wikipedia dentro il ciclo di richiesta vanno
  minimizzate: usa `_get_or_refresh_person` (freshness via
  `wikidata_check_interval_hours`), il summary lazy e i batch ≤ 10 per il
  diff bulk. Mai loop illimitati di fetch in una singola richiesta.

## Test

La suite di test è divisa su più file. `game/tests.py` copre il calcolo del
punteggio in modo esaustivo, più email transazionali, reminder sostituzioni
e preferenze tema:

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
- `ThemePreferenceTest`: default e valori validi di `theme_preference`
- `DeathEmailTest`: opt-in/out email, subject "urgent", trigger dal signal
  sulla transizione `is_confirmed`, no-crash se l'email non è configurata
- `SubstitutionReminderTest`: soglie T-3/T-1, idempotenza via
  `SubstitutionReminder`

Altri file di test:
- `game/tests_commands.py`: management command `check_deaths` (auto-conferma,
  `--no-autoconfirm`, `--force`, `--dry-run`)
- `game/tests_middleware.py`: `LoginRequiredEverywhereMiddleware` (path
  pubblici vs protetti)
- `game/tests_views.py`: permessi/integrazione delle view (in arrivo,
  copertura ancora parziale)
- `wikidata_api/tests.py`: client Wikidata con chiamate HTTP mockate

**Aree ancora poco coperte**: view (integrazione, in corso in
`tests_views.py`), admin actions.

Esegui i test con:
```bash
python manage.py test game wikidata_api
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
python manage.py check_deaths              # rileva morti via Wikidata (auto-conferma se data valida)
python manage.py check_deaths --dry-run    # senza scrivere
python manage.py check_deaths --force      # ignora data_frozen e last_checked sulle persone
python manage.py check_deaths --no-autoconfirm   # crea i decessi non confermati
python manage.py mark_originals            # a inizio stagione di una lega
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

- Inviti via email per leghe private (oggi codice condiviso + link invito
  diretto; le email transazionali di decesso/reminder sono già implementate
  in `game/email.py`)
- Coprire le admin actions con test
- Indici DB su `Death.death_date`, `Team.league`, `LeagueMembership.user`
  se le leghe diventano numerose
- API REST con DRF se serve un'app mobile nativa
