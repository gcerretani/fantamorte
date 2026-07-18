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
- **Design system CSS custom** (`static/css/fantamorte.css`, nessuna
  dipendenza esterna) + vanilla JS per il frontend (server-rendered, no SPA,
  no bundler). Il vocabolario di classi (`btn`, `card`, `badge`,
  `list-group`, `form-control`, `nav-tabs`, `modal`…) è ereditato da
  Bootstrap ma le regole sono nostre: vedi "Frontend conventions".
- **django-allauth** per auth + OAuth (Google, GitHub); form con classi CSS
  (`form-control`/`form-check-input`/`is-invalid`) applicate server-side via
  `ACCOUNT_FORMS` → `game/forms.py`
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
│   ├── forms.py             # Form allauth con classi CSS del design system (ACCOUNT_FORMS)
│   ├── scoring.py           # Calcolo punteggi (sorgente di verità: la League)
│   ├── person_sync.py       # Core UNICO di sync persona da Wikidata (campi, claims, Death)
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
├── scripts/
│   └── generate_pwa_icons.py # Rigenera le PNG PWA dagli SVG (cairosvg)
├── templates/
│   ├── base.html            # Layout, top bar, sprite icone, modal persona, dark mode
│   ├── _logo.html           # Marchio: teschio SVG inline (mai entità/emoji)
│   ├── _bottom_nav.html     # Bottom nav mobile (4 tab, active_nav)
│   ├── account/             # Override allauth (login/signup) con stile del design system
│   ├── email/               # Template email transazionali (txt + html)
│   └── game/                # Tutti i template della app (+ sw.js renderizzato)
├── static/
│   ├── css/fantamorte.css   # Tema custom + dark mode + componenti
│   ├── js/fantamorte.js     # SW reg, push, install prompt, modal persona, countdown
│   └── pwa/                 # icone manifest + apple-touch + svg
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh            # Migrations + collectstatic + gunicorn
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

- **`League`** ha `start_date`, `end_date`, `registration_opens/closes`
  (vincolo validato in `update_rules`: `registration_closes ≤ start_date`,
  le squadre devono essere definitive quando i decessi iniziano a contare),
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
  La finestra di modifica (registrazioni aperte, nessun lock) è
  `_team_edit_window_open`; `_can_edit_team` la applica al solo manager,
  **senza override per lo staff**: la UI di gioco è identica per tutti e
  gli interventi eccezionali sulle rose si fanno dal Django admin.
  Lo staff mantiene invece i poteri dell'owner su ogni **lega** (ruoli,
  trasferimento proprietà, eliminazione): il pannello admin passa
  `can_manage_league` (owner o staff) ai template. Lì il Django admin non
  è equivalente (delete con pulizia dei DeathBonus protetti, transfer
  coerente su owner+membership, validazione P/Q dei bonus custom).
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
  per il Grammy) oppure, lasciando vuota la proprietà, come bonus **manuali**
  che gli admin di lega assegnano ai decessi dalla pagina
  `/leghe/<slug>/decessi/` (POST `assign_bonus`/`remove_bonus`; assegnabili a
  mano anche i bonus `wikidata`/`age` se la detection ha mancato il dato, mai
  gli speciali `original`/`first_death`/`last_death`). Nei template il criterio
  di assegnazione è renderizzato dal partial `_bonus_detection.html`, che legge
  i campi reali della detection (non la `description`, che è solo testo). La
  detection `wikidata` prova prima il match esatto sui
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
- **`UserProfile`** tiene le preferenze per-utente: `theme_preference`
  (`auto|light|dark`) e `notification_prefs` (JSON), la **matrice canali per
  categoria** `{categoria: {push: bool, email: bool}}`. Le categorie e i default
  vivono in un unico punto (`default_notification_prefs()` in `models.py` +
  `NOTIFICATION_CATEGORIES` in `game/notifications.py`); `profile.wants(cat,
  channel)` è il gate letto da push/email. Il **feed in-app è sempre attivo** e
  non compare nella matrice. Creato automaticamente al signup via signal.
  (I vecchi booleani `push_/email_notifications_enabled` sono stati **rimossi** e
  migrati nella matrice: migration `0019`, data migration inclusa.)
- **`Notification`** è la riga del **feed in-app** (inbox stile social) di un
  utente: `kind` (choices), `title`/`body`/`url` **denormalizzati**, `is_urgent`,
  `is_read`, `created_at`, più FK opzionali `death`/`league` per dedup e
  navigazione. È la **sorgente di verità**: ogni evento crea prima una riga qui,
  poi push/email sono canali sopra (vedi `game/notifications.py`).
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

- **Manifest** servito da `/manifest.webmanifest` (rendering JSON, view in
  `views.py`). Gli URL delle icone passano da `static()` (hashati in
  produzione, come il precache del SW). Include le icone `maskable`
  (adattive Android, glifo in safe-zone) e `monochrome`.
- **Icone**: sorgenti SVG in `static/pwa/` (`icon.svg` = brand,
  `badge.svg` = silhouette trasparente); le PNG derivate (192/512,
  maskable, badge-96, apple-touch) si rigenerano con
  `python scripts/generate_pwa_icons.py` (cairosvg). Il **badge** delle
  notifiche Android usa solo il canale alpha: deve restare il PNG
  monocromatico trasparente — mai puntarlo all'icona quadrata opaca
  (tornerebbe il bug del quadrato bianco).
- **Service worker** servito da `/sw.js` (template Django, niente static).
  Cache offline: network-first per HTML, cache-first per asset; gestisce
  push (`icon`/`badge` con override opzionale dal payload).
  Il `cache_version` nel nome della cache è parametrico nel template Django
  per evitare stale assets. Gli asset propri nel precache passano da
  `{% static %}`: in produzione risolvono ai nomi con hash del
  ManifestStaticFilesStorage (i path non hashati sarebbero a rischio stale,
  nginx li serve con cache 30 giorni).
- **Static in produzione**: `STATIC_ROOT` è un named volume condiviso con
  nginx che **oscura** a ogni deploy il collectstatic fatto in build:
  per questo `entrypoint.sh` riesegue `collectstatic --noinput` a ogni
  avvio del container web. Non rimuoverlo.
- **VAPID**: chiavi in env (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`,
  `VAPID_CLAIM_EMAIL`). Genera con `python manage.py generate_vapid_keys`.
  Senza VAPID, i tentativi di push sono no-op (non crashano).
- **Trigger push**: signal post_save su `Death`. Quando passa
  `is_confirmed=False → True`, `game.push.broadcast_death_notification`
  manda a tutti i `LeagueMembership` delle leghe il cui range contiene
  `death.death_date`. Notifica "urgent" se la persona è nella squadra
  dell'utente. Le sottoscrizioni 404/410 vengono cancellate automaticamente.

## Notifiche: architettura persist-first

**Regola:** ogni evento crea prima una riga `Notification` (il **feed in-app**,
sempre attivo), poi push ed email sono **canali di consegna** sopra la stessa
risoluzione di destinatari. Tutto è centralizzato in **`game/notifications.py`**
(niente terzo percorso parallelo): risoluzione destinatari decesso
(`leagues_for_death`, `affected_manager_ids`, `death_member_user_ids`, riusati
da push/email), creazione righe feed (`create_death_notifications`,
`create_substitution_notification`, `notify_league_joined`, `notify_team_locked`,
`emit_league_lifecycle_notifications`), gating canali (`wants(user, cat,
channel)`) e helper badge (`unread_count`, `mark_all_read`).

- **Sorgenti degli eventi**: decessi → `signals.notify_on_death_confirmed`
  (crea feed + push + email); reminder sostituzione → command
  `send_substitution_reminders`; iscrizione lega → signal `post_save`
  `LeagueMembership` (notifica l'owner); blocco squadra → signal `post_save`
  `Team` sulla transizione `is_locked` (pattern `_was_locked`, come
  `_was_confirmed`); inizio/fine lega → command `emit_league_lifecycle`.
- **Canali per categoria**: `UserProfile.notification_prefs` è la matrice
  (categoria × canale). Il feed **non** è nella matrice (sempre attivo); push ed
  email si inviano solo se `wants()` è vero. Default: decessi+sostituzione su
  push+email, eventi lega solo in-app.
- **Feed UI**: campanella `fixed-top` (base.html, badge `unread_notifications_count`
  dal context processor) → pagina `/notifiche/` (segna lette al load). Il badge
  si aggiorna senza reload su `visibilitychange` (`fmUpdateNotifBadge`).
- **Preferenze UI (profilo)**: interruttore push **per-dispositivo** (subscribe/
  unsubscribe, stato sincronizzato da `fmSyncPushSwitch`, riga dispositivi via
  `/api/push/devices/`, niente refresh) + **matrice** categoria×canale con
  **autosave** (`fmSavePreference` → `/api/profilo/preferenze/`). Nessun pulsante
  "Salva".
- **Pronto per app native**: il feed persistente + gli endpoint
  `/api/notifications/*` sono la superficie API-first che un futuro client
  nativo (o PWA in store) consuma. Aggiungere FCM (Android) / APNs (iOS) sarà un
  **nuovo canale** accanto a Web Push, riusando `Notification` e la risoluzione
  destinatari; `PushSubscription` è generalizzabile a "device token". Nota: Web
  Push su iOS richiede PWA installata (16.4+); su Android funziona anche via TWA.

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
- `get_entity(qid)`: fetch completo (labels, claims, immagine Commons, occupazione, nazionalità, URL Wikipedia).
  Fallback label/descrizione: `it → mul → en → QID` — la lingua speciale `mul`
  è la label "default per tutte le lingue" di Wikidata, certe entità hanno solo quella
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
- **Tutti i percorsi che applicano dati Wikidata a una persona passano da
  `game/person_sync.py`** (`sync_person_from_entity`): campi anagrafici (con
  guardia sui None), `claims_cache` + invalidazione cache bonus derivate,
  `last_checked`, registrazione `Death` con auto-detect bonus e conferma.
  I chiamanti sono tre — il cron `check_deaths`, l'endpoint sync della
  pagina admin giocatori, `_get_or_refresh_person` (aggiunta in rosa) — e
  differiscono solo per la *strategia di selezione* delle persone, mai per
  come applicano i dati. Non aggiungere percorsi di scrittura paralleli.
- L'endpoint sync giocatori accetta **max 10 persone per richiesta**
  (`MAX_DIFF_BATCH`): il fan-out lo fa il browser a blocchi con concorrenza
  2 (vedi `league_players_refresh.html`), mai una singola richiesta lunga.
  Le persone `data_frozen` vengono saltate anche dalla sync manuale.

## URL principali (mappa)

```
/                               home (dashboard utente)
/leghe/                         lista leghe pubbliche
/leghe/nuova/                   crea lega
/leghe/<slug>/                  detail (top 3 + recent deaths + regole + iscrizione)
/leghe/<slug>/admin/            pannello admin (regole, bonus, membri, invito, danger zone)
/leghe/<slug>/elimina/          POST: elimina la lega (owner o staff, richiede il nome digitato)
/leghe/<slug>/regolamento/      riepilogo regole+bonus della lega (visibile a tutti i membri)
/leghe/<slug>/classifica/       classifica completa
/leghe/<slug>/decessi/          timeline decessi (con assegnazione manuale bonus per gli admin)
/leghe/<slug>/giocatori/        sync Wikidata giocatori della lega (admin)
/leghe/<slug>/squadra/nuova/    crea la mia squadra in questa lega

/squadra/<pk>/                  dettaglio squadra
/squadra/<pk>/modifica/         edit squadra (rosa, capitano, jolly)
/squadra/<pk>/aggiungi/         POST AJAX: aggiunge persona (Wikidata)
/squadra/<pk>/rimuovi/<member_pk>/        POST AJAX: rimuove persona (solo fase composizione, mai morti/subentrati)
/squadra/<pk>/sostituisci/<member_pk>/    flusso sostituzione
/squadra/<pk>/what-if/          simulatore punti (capitano/jolly + bonus automatici di lega)

/persona/<pk>/                  pagina dettaglio (con bio Wikipedia)
/morte/<pk>/                    dettaglio decesso con bonus e squadre coinvolte
/api/persona/<pk>/              JSON per il modal (solo dati in DB + summary_stale; con
                                ?league=<slug> aggiunge i bonus automatici "se morisse oggi")
/api/persona/<pk>/summary/      refresh sincrono del summary Wikipedia (lazy dal modal)
/api/search-person/             autocomplete Wikidata (accetta ?q=&league=<slug> per filtrare per lingua)
/api/leghe/<slug>/wikidata-diff/    JSON POST: sync Wikidata + report differenze (admin, max 10 persone)

/profilo/                       preferenze utente (push per-dispositivo, matrice canali, tema)
/api/profilo/preferenze/        POST JSON: autosave preferenze (tema + matrice notifiche)
/notifiche/                     feed notifiche in-app (segna lette al load)
/api/notifications/             JSON: lista notifiche (paginata; per live/native)
/api/notifications/unread-count/  JSON: conteggio non-lette (badge campanella)
/api/notifications/mark-read/   POST: segna lette (tutte o lista ids)
/statistiche/                   statistiche cross-lega (morituri più giocati/redditizi,
                                record decessi, bonus frequenti, storico + leaderboard all-time)
/regolamento/                   manuale generico del portale (nessun punteggio: quelli sono per-lega)
/healthz/                       healthcheck (pubblico, verifica anche il DB)

/api/push/{subscribe,unsubscribe,test,devices}/    (devices = lista live per la UI)

/manifest.webmanifest, /sw.js, /offline/    PWA
/accounts/...                   allauth (login, signup, password reset, social)
/admin/                         Django admin
```

## Frontend conventions

- **Design system CSS custom** (`static/css/fantamorte.css`, ~1000 righe,
  nessuna dipendenza esterna, niente bundler/CDN). È organizzato in sezioni
  numerate: ① token per tema, ② reset+base, ③ tipografia, ④ utility,
  ⑤ griglia, ⑥ form, ⑦ componenti (a vocabolario ereditato), ⑧ componenti
  `fm-*`, ⑨ responsive. **Il vocabolario di classi è quello di Bootstrap**
  (`btn`, `btn-primary`, `card`, `badge text-bg-*`, `list-group`,
  `form-control`, `alert`, `nav-tabs`, `nav-pills`, `modal`, `collapse`,
  `dropdown`, `breadcrumb`, `spinner-border`, `placeholder`, `toast`,
  utility `d-flex`/`mb-3`/`col-md-6`…) ma le regole sono NOSTRE: non esiste
  più alcun CSS Bootstrap. Se aggiungi markup, usa una classe già definita
  nel foglio; se ne serve una nuova, definiscila lì.
- **Pelle «Notturno» (dark-first) via token semantici `--fm-*`**: la palette
  vive TUTTA nella sezione ① in testa a `fantamorte.css` — grafite+ottone
  come tema primario, variante chiara "osso" — con `--fm-ground/surface/
  ink/muted/line/accent(+-rgb/-ink/-hover)/danger/success/info/warning/
  radius*/topbar*/theme-color`. Tutte le regole leggono questi token: per
  ritoccare la palette si toccano **solo** i due blocchi token
  (`:root`/`[data-fm-theme=light]` e `[data-fm-theme=dark]`). I colori vanno
  tenuti in sync con `PWA_APP_THEME_COLOR`/`PWA_APP_BACKGROUND_COLOR` in
  `settings.py`, il fallback hex in `fantamorte.js` e gli SVG in
  `static/pwa/` (rigenera le PNG con `python scripts/generate_pwa_icons.py`,
  richiede cairosvg).
- **Dark mode**: lo script anti-FOUC in `base.html` (e il toggle in
  `fantamorte.js`) scrivono `data-fm-theme="light|dark"` su `<html>`; la
  preferenza tri-state (`auto|light|dark`) sta in `data-theme-pref` +
  localStorage. Il meta `theme-color` viene riscritto dal toggle leggendo il
  token CSS `--fm-theme-color`. Ogni componente si adatta ai due temi
  leggendo i token: non aggiungere override tema-specifici fuori dalla
  sezione ①.
- **Behaviors JS (ex-Bootstrap)**: modal, collapse, dropdown, tab e i
  pulsanti di chiusura sono implementati in vanilla nella sezione "UI
  behaviors" di `fantamorte.js`. Attributi dichiarativi:
  `data-fm-toggle="collapse|dropdown|tab"` (+ `data-fm-target="#id"` o
  `href="#id"` per i tab) e `data-fm-dismiss="alert|modal|toast"`. Il modal
  si apre anche via API: `window.fmModal.show(el)`/`.hide(el)` (usato da
  `fmShowPerson`). Non reintrodurre Bootstrap: aggiungi comportamenti qui.
- **Logo**: sempre il partial `templates/_logo.html` (teschio SVG inline,
  `currentColor`, classe `.fm-logo`) — mai l'entità `&#9760;` o emoji per
  il brand: la resa cambierebbe da un device all'altro. Per icone inline
  nel contenuto c'è la sprite `<symbol>` in `base.html`
  (`fmIcoHome/Leagues/Stats/User/Skull`), da referenziare con
  `<svg class="fm-ico"><use href="#fmIcoSkull"/></svg>`.
- **Convenzione bottoni**: `btn-primary` per l'azione affermativa/primaria
  (Salva, Aggiungi, Iscriviti, Conferma, Crea…), `btn-outline-secondary`
  per azioni secondarie e navigazione, `btn-danger`/`btn-outline-danger`
  solo per azioni distruttive. In top bar: `btn-icon` (tondo ghost) per
  tema/installa, `btn-outline-light` per Esci/Accedi (sfondo barra scuro).
- **Convenzione badge**: sempre `text-bg-*` (mai `bg-*` nudo):
  `danger`=morte, `success`=vivo/attivo/confermato, `primary`=capitano,
  `info`=meccaniche di gioco (jolly, originale, personalizzato),
  `warning`=stati di attenzione (non confermato), `secondary`=meta
  (ruoli, punteggi, stati neutri). Sui `.badge` le classi `text-bg-*` sono
  ristilate come tinte traslucide del tema (sezione componenti di
  `fantamorte.css`); i toast le usano nella versione opaca originale.
- JS custom in `static/js/fantamorte.js`: tema, UI behaviors (modal/collapse/
  dropdown/tab/dismiss), install prompt, push, modal persona, countdown
  sostituzioni, toast, ricerca persona. Tutto attaccato a `window.fm*`
  (`fmShowPerson`, `fmModal`, `fmEnablePush`, `fmToast`, `fmPersonSearch`,
  `fmInitCountdowns`, ...).
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
  scaduta in lazy da `/api/persona/<pk>/summary/`. Il contesto lega si
  eredita dal più vicino antenato con `data-fm-league` (il `<main>` di
  base.html lo imposta quando `league` o `team` sono in contesto): con la
  lega nota, il modal mostra i bonus automatici "se morisse oggi".
- **Due barre fisse**: la top bar è `fixed-top` (mai sticky: si muoverebbe
  con l'overscroll), slim, sempre scura (`.fm-topbar`, token
  `--fm-topbar-bg`), con i link inline solo da `lg` in su; sotto `lg` la
  navigazione è la **bottom nav** `templates/_bottom_nav.html`
  (`.fm-tabbar fixed-bottom d-lg-none`, 4 tab Home/Leghe/Statistiche/
  Profilo, solo utenti autenticati). Lo stato attivo arriva dal context
  processor `active_nav` (`game/context_processors.py`, mappa
  `resolver_match.url_name`; le sottopagine di leghe/squadre/persone
  accendono il tab Leghe). Il body compensa entrambe le barre in
  `fantamorte.css` (padding-top con safe area notch, padding-bottom sotto
  `lg` con `env(safe-area-inset-bottom)`): se cambi l'altezza di una
  barra, aggiorna il padding corrispondente. Niente hamburger/offcanvas:
  su mobile "Come funziona", Django admin e logout stanno nella card
  Account del profilo.
- **Chips e tile riusabili**: metadati di pagina (periodo, iscritti, owner,
  jolly…) come chips `.fm-facts`/`.fm-fact`; numeri-chiave delle regole come
  tile `.fm-stat` via partial `_league_rules_summary.html` (usato da
  league_detail e league_scoring: non duplicare le regole nei template).
- Per il **countdown** della deadline sostituzione, usa
  `<span class="fm-countdown" data-fm-countdown="{{ deadline|date:'U' }}">…</span>`
  (initializzato da `fmInitCountdowns`, richiamabile su un sottoalbero dopo
  un replace del DOM).
- Animazioni: minime (fade modal/toast, hover liste); le transizioni non
  essenziali vanno dentro `@media (prefers-reduced-motion: no-preference)`.
- **Etichette di sezione**: usa `.fm-label` (overline monospace uppercase)
  per i titoli di lista/sezione dentro le pagine (Le mie leghe, Classifica,
  Ultimi decessi…), non un `<h4>`/`<h5>` nudo.
- **Navigazione**: ogni sottopagina apre con un
  `<header class="fm-page-header">` che contiene un breadcrumb
  (`Leghe › <lega> › <pagina>`, per le squadre `Leghe › <lega> › <squadra> ›
  <pagina>`), titolo `h2` ed eventuali badge/chips, separati dal contenuto
  da un bordo. Il breadcrumb è ristilato globalmente in `fantamorte.css`
  (compatto, una riga con ellissi, link secondari): non ripetere nel titolo
  o nelle chips informazioni già nel breadcrumb (es. il nome della lega).
  Se aggiungi una pagina sotto lega o squadra, usa lo stesso header;
  niente più bottoni "← Torna a...". Le pagine di lega includono inoltre,
  subito sotto l'header, il partial `_league_nav.html` (pill orizzontali
  scrollabili Panoramica·Classifica·Decessi·Regole·Gestione-se-admin,
  attiva da `resolver_match`): richiede `league` in contesto e `is_admin`
  per la voce Gestione; niente bottoni-scorciatoia duplicati nell'header.
- **Tabelle → liste**: i dati per-entità (classifiche, rosa, punti,
  storico, iscritti, giocatori) si mostrano come `list-group` con gli
  helper `.fm-pos`/`.fm-row-main`/`.fm-metric` (+ `.fm-rank-first`,
  `.fm-rank-me`, `.fm-row-dead`), **markup unico per tutti i breakpoint**
  (mai doppio markup `d-none d-md-block`). Le `<table>` restano solo per
  dati genuinamente tabellari; su mobile niente colonne nascoste che
  perdono informazione (usa un collapse, vedi la classifica).
- **Danger zone**: le azioni distruttive (elimina lega, elimina squadra)
  stanno in una **tab dedicata** (`.fm-tab-danger`, rossa) dei pannelli di
  modifica (league_admin e team_edit), mai tra le azioni normali, dentro
  una card `border-danger`. L'eliminazione di lega e squadra richiede di
  ridigitare il nome (validato anche server-side in `LeagueDeleteView` /
  `TeamDeleteView`; il bottone si abilita via JS al match).

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
- Per il dark mode: il valore `data-fm-theme` viene applicato inline da
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
- `game/tests_notifications.py`: feed notifiche in-app + matrice preferenze
  per canale (creazione righe alla conferma decesso, gating push/email via
  `wants`, reminder/iscrizione/blocco/lifecycle, endpoint feed e autosave)
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
python manage.py send_substitution_reminders             # reminder feed+push+email per sostituzioni (T-3, T-1)
python manage.py send_substitution_reminders --dry-run   # solo log, niente invio
python manage.py emit_league_lifecycle     # notifiche feed di inizio/fine lega (idempotente, da cron)

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
6. Static → `static/css|js|...`. Nessuna dipendenza da CDN: il frontend è
   tutto self-hosted (design system CSS + vanilla JS).
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
