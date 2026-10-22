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
│   ├── timeline.py          # Feed eventi di lega (decessi+punti, sostituzioni, squadre, iscrizioni)
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
  `search_wikipedia_langs` (CSV di wiki, es. `itwiki,enwiki`; vuoto = tutta Wikidata),
  `captain_succession` (destino della fascia quando il capitano muore:
  `substitute` = la eredita il sostituto, default; `free` = il manager la
  assegna durante la sostituzione, radio in `substitute_member.html`;
  `none` = non si riassegna. Il flag `is_captain` del membro **morto** non
  si tocca mai: lo scoring storico legge quello) e
  `secret_rosters_preseason` (se True, prima di `start_date` ogni manager
  vede solo la propria rosa: gate `League.roster_hidden_for(user, team)`
  applicato a team_detail, person_detail, death_detail, statistiche e
  pagina sync giocatori — vale anche per staff e admin di lega, che sono
  giocatori pure loro; la scappatoia resta il Django admin).
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
- **Import rosa** (`game/team_import.py`, `/squadra/<pk>/importa/`): il
  manager, a iscrizioni aperte, riparte dalla rosa di una sua squadra di
  un'altra lega. È **selettivo**: entrano solo i vivi che rispettano le
  regole della lega di destinazione — wiki ammesse (`search_wikipedia_langs`,
  verificate con `qids_with_sitelinks` in una SPARQL cachata; chi ha già
  `wikipedia_url_it` passa senza rete se `itwiki` è ammessa), stessa
  validazione di `AddPersonView` (`_validate_roster_person`), limiti di rosa
  ed età riempiti in ordine capitani→nome. Il piano (`plan_import`) si
  ricalcola al POST sotto lo stesso lock di riga di `AddPersonView`; se la
  verifica wiki non risponde non si importa nessuno.
- **`TeamMember.can_be_substituted()`** governa la sostituzione. Il discrimine
  è **quando siamo**, non quando è avvenuta la morte: prima di `start_date`
  (composizione) non si sostituisce nessuno — il deceduto si toglie dalla rosa
  e se ne sceglie un altro (`died_before_season`, bottone × in `team_edit`) —
  e dall'avvio in poi anche un decesso pre-stagione è sostituibile. Il membro
  non viene **mai** cancellato d'ufficio: `notifications.notify_preseason_dead_members`
  avvisa e basta (un tempo cancellava, e chi non poteva più intervenire —
  iscrizioni chiuse, o conferma arrivata a lega avviata — restava con la rosa
  mutilata e nessun rimedio; nessun minimo di rosa esiste, quindi si giocava
  in inferiorità per sempre).
  Attenzione a due asimmetrie non ovvie: la deadline decorre dal più tardo tra
  `Death.confirmed_at` e l'inizio della lega, **non** dalla data di morte (una
  conferma tardiva apre la finestra oggi, anche per un decesso di mesi prima;
  e un decesso pre-stagione confermato in anticipo non nasce già scaduto), e a
  **lega conclusa** (`League.is_finished()`) non si sostituisce più — quella
  finestra cadrebbe fuori dal periodo di gioco e la sostituzione non
  sposterebbe un punto, cambierebbe solo la rosa storica. Il gate è dinamico:
  prolungare `end_date` la riapre. Ne dipendono il bottone in
  `team_edit`/`team_detail`, il
  countdown, le scadenze nel feed ICS e la frase «hai N giorni per
  sostituirlo» in push ed email — che si mostra **solo** a chi ha davvero la
  persona in rosa e solo per la *sua* lega
  (`notifications.affected_manager_leagues`, `push._substitution_hint`): un
  tempo si pescava una lega qualsiasi tra quelle che contenevano la data.
- **`Team.roster_slots()`** ricostruisce la rosa come *slot*: titolare attivo
  + catena dei predecessori (`replaced_by`), in memoria sul prefetch. È la
  vista che usano `team_detail` e `team_edit` (partial `_slot_history.html`
  «al posto di X · sostituito/a il …», badge «subentrato/a»). Il dettaglio
  squadra ha poi la sezione **Caduti** (`_team_fallen` in `views.py`): ogni
  membro morto, sostituito o no, con punti e — se non ne ha portati — il
  motivo (non confermato, fuori periodo, già morto quando è subentrato).
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
  globale, ad es. `wikidata_check_interval_hours`. Tiene anche la
  **telemetria di `check_deaths`** (`death_check_*`, sola lettura in admin):
  ogni run registra successo o fallimento (`game/monitoring.py`), `/healthz/`
  espone lo stato (`ok|never|stale|failing`, senza mai cambiare lo status
  HTTP: un Wikidata giù non deve far riavviare il web) e il command
  `monitor_death_checks`, lanciato dallo scheduler dopo ogni `check_deaths`,
  manda un'email allo staff se i controlli sono fermi (> 3 cicli, min 2 h)
  o falliscono 3 volte di fila — al massimo una al giorno, riarmata alla
  ripresa. Un decesso perso in silenzio è il guasto peggiore del gioco.
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
  `last_used_at` e `auth`/`p256dh` keys. Il `user_agent` salvato al subscribe
  diventa un'etichetta leggibile (`device_label` → `describe_user_agent()` in
  `models.py`: «Chrome su Android», «Safari su iPhone»…), usata dall'elenco
  dispositivi nel profilo e dal Django admin. Le righe muoiono in due modi:
  il push service risponde 404/410 a un invio (`send_push` cancella) oppure
  l'utente revoca il dispositivo dal profilo. Non c'è scadenza a tempo: la
  rotazione dell'endpoint (evento `pushsubscriptionchange`) è gestita dal SW
  aggiornando la riga esistente, così non si accumulano dispositivi fantasma.
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
  - `/api/push/rotate/` (la chiama il service worker, che non può avere
    sessione né cookie CSRF: si autentica per capability, vedi "PWA + Push")
- **Pagine allauth nel design system**: `templates/allauth/layouts/`
  (`base` estende il nostro `base.html`; `manage` = colonna stretta sotto
  Profilo; `entrance` = card centrata) + `templates/allauth/elements/`
  (bottoni, campi via `{% fm_field %}`, badge, alert…). Così gestione email,
  cambio/impostazione password, account collegati, recupero password,
  verifica email e le pagine future di allauth escono già stilate. Le
  pagine `/profilo/nome-utente/` ed `/profilo/elimina/`
  (`game/account_settings.py`) coprono ciò che allauth non offre.
  L'eliminazione **anonimizza** (via dati personali, squadre e piazzamenti
  restano; bloccata per gli owner di leghe).
- **Login social sempre in POST**: `security_policy.py` tiene
  `SOCIALACCOUNT_LOGIN_ON_GET=False` (CSRF), quindi i bottoni dei provider
  sono form POST (`socialaccount/_provider_buttons.html`, elemento
  `provider.html`): con un link GET allauth mostrerebbe una pagina di
  conferma intermedia («Accedi tramite Google» → Continua).
- **OpenID Connect generico** (`openid_connect`: Authentik, Keycloak…) da
  env `OIDC_*` o da SocialApp in admin, come Google/GitHub.
  `PASSWORD_LOGIN_ENABLED=False` lascia solo i provider (form nascosto,
  POST rifiutato in `game/forms.py`, signup via form chiuso), ma solo se ce
  n'è almeno uno: `adapters.password_login_enabled`.
- **Avatar dei manager** (`game/user_avatars.py`, `{% user_avatar user %}`,
  stesso partial delle persone): scelta in `UserProfile.avatar_source`
  (auto = foto dell'account collegato, altrimenti Gravatar; Gravatar; solo
  iniziali). La foto social è copiata in `social_avatar_url` dai signal
  allauth (niente query per manager nelle classifiche); Gravatar con
  `d=404`, così senza Gravatar restano le iniziali.
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
- **Rotazione endpoint**: il SW gestisce `pushsubscriptionchange` (il browser
  può rigenerare la subscription da solo) e chiama `POST /api/push/rotate/`
  con `{old_endpoint, subscription}`; la view aggiorna **in place** la riga
  esistente. Senza questo, la riga vecchia resterebbe in DB fino al primo
  410 e il profilo mostrerebbe dispositivi fantasma. Un SW non ha DOM e non
  può leggere il cookie CSRF, quindi la rotta è `csrf_exempt` + pubblica e si
  autentica **per capability**: si accetta solo un `old_endpoint` già
  presente in tabella (una URL push è un segreto non indovinabile, e la riga
  trovata dice già di chi è) e non si creano mai righe nuove. Non esentare
  invece `/api/push/subscribe/`, che è autenticata a sessione: sarebbe un
  buco CSRF che permette di registrare l'endpoint di un attaccante per la
  vittima. Il SW ha bisogno della chiave VAPID per re-iscriversi:
  `ServiceWorkerView` la passa nel context (`vapid_public_key`).
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
- **Nuova edizione di una lega** (`game/league_clone.py`): copia la
  *configurazione* (`RULE_FIELDS`, bonus attivi con override, bonus
  personalizzati **duplicati** sulla nuova lega) e mai il *gioco* (squadre,
  iscritti, decessi). L'invito agli iscritti è l'evento
  `league_invite` (`notify_league_invite`, dedup per utente+lega, link con
  codice invito se privata), default push+email attivi.
- **Canali per categoria**: `UserProfile.notification_prefs` è la matrice
  (categoria × canale). Il feed **non** è nella matrice (sempre attivo); push ed
  email si inviano solo se `wants()` è vero. Default: decessi+sostituzione su
  push+email, eventi lega solo in-app.
- **Card «lutto»** (`{% mourning_overlay %}` in `base.html`, partial
  `_mourning.html`): al primo caricamento dopo un decesso confermato nella
  tua rosa, overlay con ritratto, date e punti portati (contatore animato,
  fermo con `prefers-reduced-motion`). Sorgente = la `Notification`
  `death_team` non letta più recente: chiuderla la segna letta, quindi si
  vede una volta sola. Nessuno stato nuovo; mai sulla pagina `/notifiche/`.
- **Feed UI**: campanella `fixed-top` (base.html, badge `unread_notifications_count`
  dal context processor) → pagina `/notifiche/` (segna lette al load). Il badge
  si aggiorna senza reload su `visibilitychange` (`fmUpdateNotifBadge`).
- **Preferenze UI (profilo)**: interruttore push **per-dispositivo** (subscribe/
  unsubscribe, stato sincronizzato da `fmSyncPushSwitch`) + **elenco dispositivi**
  con revoca per riga + **matrice** categoria×canale con **autosave**
  (`fmSavePreference` → `/api/profilo/preferenze/`). Nessun pulsante "Salva".
- **Elenco dispositivi**: il partial `templates/game/_push_devices.html` è
  l'**unico renderer** (etichetta, data di registrazione, ultima notifica
  consegnata, badge "questo dispositivo", bottone Revoca). Lo include
  `ProfileView` al primo paint e lo restituisce già renderizzato
  `/api/push/devices/` nella chiave `html`, che `fmRefreshPushDevices`
  inietta in `#fmDevicesRegion` senza reload. Non riscrivere quelle stringhe
  nel JS: la riga di conteggio era duplicata server+client e il plurale era
  divergente («dispositivo/i»). Il marcatore "questo dispositivo" arriva dal
  `?endpoint=` che il client passa (il server confronta solo dentro le
  subscription di quell'utente, non espone endpoint altrui). Il bottone
  Revoca ha un listener **delegato** in `fantamorte.js` perché il blocco
  viene sostituito a ogni refresh; se la riga è quella corrente passa da
  `fmDisablePush` (serve anche il `sub.unsubscribe()` lato browser, altrimenti
  l'interruttore master resta acceso).
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
   lega** (primo/ultimo decesso confermato nel periodo della lega **tra le
   persone presenti in almeno una rosa della lega**; l'ultimo solo a lega
   conclusa). Non esistono righe `DeathBonus` per questi tipi: le leghe
   condividono solo il database degli eventi, nessuna correlazione.
5. Moltiplicatore = `captain_multiplier` (se capitano) × `jolly_multiplier`
   (se mese jolly) — moltiplicano tra loro (es. entrambi attivi = 4×).
6. Le morti considerate sono solo quelle con `is_confirmed=True`,
   `start_date ≤ death_date ≤ end_date` della lega e persona presente in
   almeno una rosa della lega (stesso filtro usato dalla timeline decessi,
   dal dettaglio lega e dal CSV: un decesso giocato solo in altre leghe non
   compare e non conta).

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
- `check_deaths_batch(qids)`: **una** query SPARQL per lotto — «di questi, a chi
  è comparsa una `P570`?». Nessun filtro sull'anno: i candidati sono già solo
  persone che il DB crede vive, e il periodo di gioco lo applica lo scoring.
  Filtrare per anno della lega costava una query per anno e creava due punti
  ciechi (decessi dell'anno precedente all'avvio, e leghe non ancora iniziate,
  i cui anni stanno nel futuro). Stessa logica per la selezione dei candidati:
  nessun filtro sullo stato della lega, vedi `check_deaths`. Il metodo spezza
  da solo lotti oltre `DEATH_CHECK_CHUNK_SIZE` (200 QID) in più richieste: con
  `--force` (niente rotazione a fette) il pool può superare quella soglia su
  un'istanza con molte leghe storiche, e una singola query enorme è più lenta
  e più a rischio di timeout lato WDQS
- `detect_bonuses(qid, claims_cache, bonus_types)`: verifica proprietà Wikidata per i bonus
- `detect_age_bonus(age, bonus_type)`: valuta formula età con whitelist

Le query SPARQL sono template in `wikidata_api/sparql.py`:
`DEATH_CHECK_QUERY`, `HUMAN_SEARCH_QUERY` (nuova).

Config in `settings.py`: `WIKIDATA_USER_AGENT` (default `'Fantamorte/1.0'`),
`WIKIDATA_REQUEST_DELAY` (0.5 s di rate limit tra richieste; azzerato per ricerche
interattive).

Note di efficienza (importanti se tocchi il client):
- La `requests.Session` è **condivisa a livello di modulo** (riuso
  connessioni/TLS) con retry automatico su errori di connessione e su
  429/500/502/503/504 (il 429 è il throttling di WDQS: `Retry-After` viene
  rispettato invece di far fallire la richiesta). Nei test usa
  `_reset_session_for_tests()`.
- Il rate limit (`_throttle`) si applica solo **tra richieste consecutive**,
  mai prima della prima: le viste interattive non pagano lo sleep.
- Timeout per-istanza (`client.timeout`, `client.sparql_timeout`, default
  15/30 s): `PersonSearchView` li abbassa a 5/8 s per fallire in fretta.
- Le query SPARQL vanno in **POST** (`_sparql`): in GET la query sta nella
  query string e sbatterebbe contro il tetto di lunghezza dell'URI (~8 KB sui
  server Wikimedia) con un `VALUES` di qualche centinaio di QID.
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
/leghe/<slug>/                  detail (top 3 + timeline eventi lega + regole + iscrizione)
/leghe/<slug>/admin/            pannello admin (regole, bonus, membri, invito, danger zone)
/leghe/<slug>/elimina/          POST: elimina la lega (owner o staff, richiede il nome digitato)
/leghe/<slug>/duplica/          nuova edizione: lega nuova con regole+bonus di questa (admin di lega)
/leghe/<slug>/regolamento/      riepilogo regole+bonus della lega (visibile a tutti i membri)
/leghe/<slug>/classifica/       classifica completa
/leghe/<slug>/decessi/          timeline decessi (con assegnazione manuale bonus per gli admin)
/leghe/<slug>/giocatori/        sync Wikidata giocatori della lega (admin)
/leghe/<slug>/squadra/nuova/    crea la mia squadra in questa lega

/squadra/<pk>/                  dettaglio squadra
/squadra/<pk>/modifica/         edit squadra (rosa, capitano, jolly)
/squadra/<pk>/aggiungi/         POST AJAX: aggiunge persona (Wikidata)
/squadra/<pk>/importa/          import selettivo da un'altra squadra del manager (?da=<pk>)
/squadra/<pk>/rimuovi/<member_pk>/        POST AJAX: rimuove persona (solo fase composizione, mai morti/subentrati)
/squadra/<pk>/sostituisci/<member_pk>/    flusso sostituzione
/squadra/<pk>/what-if/          simulatore punti (capitano/jolly + bonus automatici di lega)

/persona/<pk>/                  pagina UNICA della persona: anagrafica, bio Wikipedia,
                                sezione #decesso (bonus + squadre a punti / fuori periodo),
                                rose che la schierano. Con ?league=<slug> ricorda la lega
                                di provenienza (breadcrumb) e, se viva, mostra i bonus
                                "se morisse oggi"
/morte/<pk>/                    redirect 302 a /persona/<pk>/#decesso (il decesso è un
                                OneToOne con la persona: nessuna pagina propria). La rotta
                                resta perché i link già spediti (email, push) e quelli
                                salvati in DB (Notification.url) puntano ancora qui
/api/persona/<pk>/              JSON per il modal (solo dati in DB + summary_stale; con
                                ?league=<slug> aggiunge i bonus automatici "se morisse oggi")
/api/persona/<pk>/summary/      refresh sincrono del summary Wikipedia (lazy dal modal)
/img/persona/<pk>/<w>/<hash>/   thumbnail della foto servita dalla cache locale
/api/search-person/             autocomplete Wikidata (accetta ?q=&league=<slug> per filtrare per lingua)
/api/leghe/<slug>/wikidata-diff/    JSON POST: sync Wikidata + report differenze (admin, max 10 persone)

/profilo/                       preferenze utente (push per-dispositivo, matrice canali, tema,
                                immagine, account: nome utente/email/password/account collegati)
/profilo/nome-utente/           cambio nome utente
/profilo/elimina/               eliminazione (anonimizzazione) dell'account
/api/profilo/preferenze/        POST JSON: autosave preferenze (tema + matrice notifiche)
/notifiche/                     feed notifiche in-app (segna lette al load)
/api/notifications/             JSON: lista notifiche (paginata; per live/native)
/api/notifications/unread-count/  JSON: conteggio non-lette (badge campanella)
/api/notifications/mark-read/   POST: segna lette (tutte o lista ids)
/statistiche/                   statistiche cross-lega (morituri più giocati/redditizi,
                                record decessi, bonus frequenti, storico + palmarès +
                                leaderboard all-time; palmarès anche nel profilo)
/regolamento/                   manuale generico del portale (nessun punteggio: quelli sono per-lega)
/healthz/                       healthcheck (pubblico, verifica anche il DB; riporta lo stato di check_deaths)

/api/push/{subscribe,unsubscribe,test,devices}/    (devices = elenco live: JSON + frammento HTML)
/api/push/devices/<pk>/revoca/  POST: revoca un dispositivo dell'utente (404 se non è suo)
/api/push/rotate/               POST dal service worker su pushsubscriptionchange
                                (pubblico + csrf_exempt, autenticato per capability)

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
  `AbortController`, errori inline; metodo `reset()`). Ogni risultato ha
  foto (P18, thumbnail 120 px) e anno di nascita, presi nella stessa
  `HUMAN_SEARCH_QUERY` (`SAMPLE`/`GROUP BY`): servono a distinguere gli
  omonimi; l'avatar JS replica il partial `_person_avatar.html`. Risultati cachati
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
  accendono il tab Leghe). Niente hamburger/offcanvas: su mobile "Come
  funziona", Django admin e logout stanno nella card Account del profilo.
- **Geometria delle barre e safe area**: altezze e safe area sono **token**
  nella sezione ① di `fantamorte.css` (`--fm-topbar-h`, `--fm-tabbar-h`,
  `--fm-bar-gap`, `--fm-safe-top/right/bottom/left`,
  `--fm-topbar-pad-top`, `--fm-tabbar-pad-bottom`). Il padding del body e
  lo `scroll-padding` di `html` **derivano** da quei token: per cambiare
  l'altezza di una barra si tocca solo il token, non due numeri da tenere
  in sync. Nessuna regola scrive `env(safe-area-inset-*)` direttamente —
  passa dai token, così un test in browser può simulare un device con
  notch sovrascrivendoli.
- **iOS: mai `black-translucent`.** In `base.html` la status bar è
  `apple-mobile-web-app-status-bar-style="black"`. Con `black-translucent`
  iOS estende la webview sotto la status bar ma riporta
  `env(safe-area-inset-top)=0` in standalone: la top bar finisce dietro il
  notch / l'isola dinamica. `viewport-fit=cover` invece serve e resta (safe
  area laterali in landscape e home indicator in basso).
- **Overscroll**: `html`/`body` hanno `overscroll-behavior: none`. L'overscroll
  del documento (stretch elastico su Android, bounce su iOS) trascina con sé
  anche gli elementi `position: fixed`: senza quella riga, a fondo pagina la
  bottom nav si stira e la top bar scorre via con effetto molla. Non è
  rimediabile dal lato delle barre. Effetto collaterale accettato: niente
  pull-to-refresh su Chrome Android.
- **Chips e tile riusabili**: metadati di pagina (periodo, iscritti, owner,
  jolly…) come chips `.fm-facts`/`.fm-fact`; numeri-chiave delle regole come
  tile `.fm-stat` via partial `_league_rules_summary.html` (usato da
  league_detail e league_scoring: non duplicare le regole nei template).
- **Foto persone: cache locale + attribuzione** (`game/person_images.py`):
  l'avatar punta a `/img/persona/<pk>/<w>/<hash>/` (whitelist 120/250/500;
  `hash` = sha256 di `image_url`, quindi URL immutabile e cache lunga
  anche nel SW). Primo accesso: download da Commons (solo
  `Special:FilePath`, redirect solo verso `*.wikimedia.org`, solo raster —
  mai SVG dal nostro dominio —, max 3 MB), salvataggio atomico in
  `PERSON_IMAGE_CACHE_DIR` (volume `person_images` nel compose); errori →
  404 `no-store` + 10 minuti senza ritentare. `PERSON_IMAGE_PROXY=False`
  torna all'hotlink. L'**attribuzione** (autore/licenza/pagina, da
  `extmetadata`) sta in `WikipediaPerson.image_credit` con il `file` a cui
  si riferisce: la rinnova il cron `refresh_image_credits` (blocchi da 50)
  e, se manca, la pagina persona; partial `_image_credit.html` sotto il
  ritratto e nel modal. I risultati della ricerca (persone non ancora in
  DB) restano in hotlink.
- **Avatar persona**: sempre `{% load fantamorte_tags %}` +
  `{% person_avatar person 'sm|md|lg|xl' %}` (partial `_person_avatar.html`,
  classi `.fm-avatar*`), mai un `<img src="{{ person.image_url }}">` a mano.
  Il tag riscrive l'URL `Special:FilePath` alla larghezza giusta per la taglia
  (bucket standard Wikimedia 120/250/500, `commons_thumb`), mette le iniziali
  su tinta deterministica sotto la foto (fallback se manca o non carica: il
  listener `error` in `fantamorte.js` rimuove l'`<img>`) e rende i morti in
  bianco e nero con anello in accento. Il modal persona riceve lo stesso
  markup già renderizzato (`avatar_html` in `/api/persona/<pk>/`).
- Per il **countdown** della deadline sostituzione, usa
  `<span class="fm-countdown" data-fm-countdown="{{ deadline|date:'U' }}">…</span>`
  (initializzato da `fmInitCountdowns`, richiamabile su un sottoalbero dopo
  un replace del DOM).
- Animazioni: minime (fade modal/toast, hover liste); le transizioni non
  essenziali vanno dentro `@media (prefers-reduced-motion: no-preference)`.
- **Font da titolo**: Fraunces variabile self-hosted (`static/fonts/`, OFL,
  sottoinsiemi latin/latin-ext) via token `--fm-font-display`, solo su
  `h1-h3`, `.display-5`, `.fm-display` e i numeri-vetrina (hero, podio): il
  resto dell'UI resta sans. Preload in `base.html`, precache nel SW.
- **Niente emoji nei template** (resa diversa per device, come per il logo):
  icone di contenuto dallo sprite di `base.html` (`fmIcoCalendar/Users/Lock/
  Jolly/Swap/Star/Crown/Pen` oltre a quelle di navigazione).
- **Stati vuoti**: partial `_empty_state.html` (icona sprite + titolo +
  testo + azioni opzionali), mai un `<p>` grigio isolato.
- **Home**: hero (`_home_hero` in `views.py`) con l'ultimo decesso nelle
  leghe in corso e la sostituzione più urgente; la lista leghe mostra la
  posizione in classifica. **Classifica**: podio `.fm-podium` sopra la lista.
- **Andamento punti**: `charts.step_chart` (linee a gradini, coordinate SVG
  calcolate in Python, numeri come stringhe col punto) + partial
  `_step_chart.html` (SVG `preserveAspectRatio="none"` +
  `vector-effect="non-scaling-stroke"`, etichette fuori dall'SVG, colori
  `.fm-tone-N` dai token). Nel dettaglio squadra e in classifica (prime 5 +
  la tua, evidenziata). Si ferma a oggi per le leghe in corso.
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
  l'argomento `--league <slug>` o, in mancanza, prendono le leghe in corso —
  tranne `check_deaths`, che **non filtra affatto per lega**: controlla chi è
  membro attivo di una rosa qualsiasi. Filtrare per stato della lega lasciava
  scoperti sia i decessi in fase di composizione (lega non ancora iniziata,
  quando basterebbe togliere la persona dalla rosa) sia quelli registrati su
  Wikidata *dopo* la fine della lega ma avvenuti durante il periodo di gioco —
  che valgono punti, quindi la classifica finale restava sbagliata.
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
  `--no-autoconfirm`, `--force`, `--dry-run`); `CheckDeathsSelezioneLegheTest`
  copre la selezione dei candidati (rose di leghe da iniziare, in corso e
  concluse; `--league` che restringe), la query unica senza filtro sull'anno e
  il decesso in stagione registrato su Wikidata dopo la fine della lega, che
  entra comunque in classifica
- `game/tests_notifications.py`: feed notifiche in-app + matrice preferenze
  per canale (creazione righe alla conferma decesso, gating push/email via
  `wants`, reminder/iscrizione/blocco/lifecycle, endpoint feed e autosave);
  `DeviceLabelTest` copre `describe_user_agent` sui casi trappola degli UA
  (Edge/Opera dichiarano Chrome, ogni browser dichiara Safari);
  `LegaConclusaTest` copre il decesso confermato in ritardo (deadline aperta ma
  lega finita): niente sostituzione, e push/email che nominano la finestra solo
  a chi ha la persona in rosa e solo per la sua lega; `PreseasonDeathTest` usa
  **date relative a oggi** (qui conta se la lega è iniziata, non l'anno nella
  fixture) e copre il decesso pre-stagione: membro mai cancellato, «toglilo
  dalla rosa» in composizione, sostituibile a lega avviata, deadline che
  decorre dall'avvio
- `game/tests_avatar.py`: avatar persona (thumbnail Commons, iniziali,
  uso in rosa/pagina persona/modal)
- `game/tests_roster_slots.py`: rosa a slot (catena sostituzioni, ordine,
  zero query sul prefetch) e sezione Caduti nel dettaglio squadra
- `game/tests_team_import.py`: import rosa (filtro wiki, morti, limiti rosa
  ed età, idempotenza, permessi, Wikidata giù)
- `game/tests_league_clone.py`: nuova edizione di una lega (regole e bonus
  copiati, personalizzati duplicati, inviti, date, permessi)
- `game/tests_restyle.py`: hero home, podio, divieto di emoji nei template
- `game/tests_person_images.py`: proxy foto (download una volta, hash,
  sicurezza del download, errori) e attribuzione Commons
- `game/tests_monitoring.py`: telemetria di `check_deaths`, stati di salute,
  `/healthz/`, avvisi email allo staff (dedup 24 h, reset alla ripresa)
- `game/tests_points_chart.py`: grafico andamento punti (gradini, scala,
  stop a oggi, prime squadre + la mia)
- `game/tests_mourning.py`: card «lutto» (compare coi punti, una volta sola,
  non per chi non ha la persona, non con decesso revocato)
- `game/tests_palmares.py`: palmarès (solo leghe concluse) in profilo e statistiche
- `game/tests_account_settings.py`: card Account, pagine allauth stilate
  (smoke), nome utente, eliminazione/anonimizzazione, bottoni social in
  POST, OIDC, login solo SSO, avatar dei manager (Gravatar/social/iniziali)
- `game/tests_middleware.py`: `LoginRequiredEverywhereMiddleware` (path
  pubblici vs protetti)
- `game/tests_views.py`: permessi/integrazione delle view (in arrivo,
  copertura ancora parziale). `PushDevicesTest` copre elenco dispositivi,
  marcatore "questo dispositivo", revoca (404 sul pk di un altro utente) e
  l'accordo singolare/plurale della riga di conteggio; `PushRotateTest` copre
  la rotazione endpoint (aggiorna in place, mai duplica, 404 se
  `old_endpoint` è ignoto); `SostituzioneLegaConclusaTest` copre il rifiuto
  della sostituzione a lega conclusa (GET/POST, rosa senza countdown, ICS
  senza scadenze) e la controprova a lega prolungata;
  `SostituzioneDecessoPreStagioneTest` copre il decesso pre-stagione dal lato
  view (rimando alla rimozione prima dell'avvio, sostituzione permessa dopo,
  bottone nella rosa solo a lega avviata)
- `wikidata_api/tests.py`: client Wikidata con chiamate HTTP mockate

**Aree ancora poco coperte**: view (integrazione, in corso in
`tests_views.py`), admin actions.

Esegui i test con:
```bash
python manage.py test game wikidata_api
# ~11 minuti in seriale; --parallel auto scende a ~3 (è quello che usa la CI)
python manage.py test game wikidata_api --parallel auto
```

La suite gira in CI su ogni push e PR verso `master`
(`.github/workflows/tests.yml`), nella **stessa** configurazione della
produzione: `DEBUG` non impostata, quindi `SECRET_KEY` obbligatoria e
`ManifestStaticFilesStorage` attivo. Da qui due step che sembrano di troppo e
non lo sono: `collectstatic` (senza manifest ogni view che usa `{% static %}`
muore con «Missing staticfiles manifest entry») e l'installazione di
`pkg-config` + `default-libmysqlclient-dev` (`mysqlclient` si compila da
sorgente e senza quelli non trova il pkg-config). C'è anche
`makemigrations --check`: un modello cambiato senza migration fa fallire la
run. Nota che `docker-publish.yml` parte sui tag `v*` ed è **indipendente**:
tagga solo commit già passati da questo workflow.

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
python manage.py check_deaths              # rileva morti via Wikidata (tutte le rose, a prescindere dallo stato della lega)
python manage.py check_deaths --dry-run    # senza scrivere
python manage.py check_deaths --force      # ignora data_frozen e last_checked sulle persone
python manage.py check_deaths --no-autoconfirm   # crea i decessi non confermati
python manage.py mark_originals            # a inizio stagione di una lega
python manage.py send_substitution_reminders             # reminder feed+push+email per sostituzioni (T-3, T-1)
python manage.py send_substitution_reminders --dry-run   # solo log, niente invio
python manage.py emit_league_lifecycle     # notifiche feed di inizio/fine lega (idempotente, da cron)
python manage.py monitor_death_checks      # email allo staff se check_deaths è fermo o fallisce (da cron)
python manage.py refresh_image_credits     # autore/licenza delle foto Commons (solo le mancanti, da cron)

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
- API REST con DRF se serve un'app mobile nativa
