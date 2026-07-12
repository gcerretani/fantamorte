# QA Checklist manuale pre-lancio — Fantamorte

Checklist di verifica manuale end-to-end prima di un rilascio in produzione
(o di un cambio rilevante). Da eseguire su un ambiente di staging il più
possibile simile alla produzione (vedi prerequisiti sotto).

Riferimenti architetturali: [`CLAUDE.md`](../CLAUDE.md).

## Prerequisiti ambiente di staging

- [ ] Backend email **SMTP reale** configurato (`EMAIL_BACKEND=...smtp...`,
      `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`,
      `DEFAULT_FROM_EMAIL`) — con il backend `console` le email non sono
      verificabili end-to-end.
- [ ] Chiavi **VAPID** generate (`python manage.py generate_vapid_keys`) e
      impostate (`VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIM_EMAIL`).
- [ ] Sito servito in **HTTPS** con dominio pubblico/staging valido
      (Web Push e "Aggiungi a Home" richiedono un contesto sicuro; `localhost`
      funziona solo per test rapidi su desktop).
- [ ] `ALLOWED_HOSTS` e `SITE_ID`/dominio allauth allineati al dominio di
      staging.
- [ ] `SW_CACHE_VERSION` impostato e diverso dal deploy precedente (per
      testare l'aggiornamento della cache del service worker).
- [ ] Almeno **2 utenti di prova** con email reali raggiungibili (o una
      mailbox di test tipo Mailtrap/Mailhog) per verificare notifiche e
      inviti incrociati.
- [ ] Almeno una **lega di prova** con date che includano "oggi" (per poter
      simulare un decesso e vederne subito l'effetto) e una in stato
      "iscrizioni aperte" per testare il join.
- [ ] Credenziali OAuth Google/GitHub configurate in staging **se** si vuole
      verificare anche il login social (altrimenti i pulsanti non compaiono:
      comportamento atteso, non un bug).
- [ ] Accesso al Django admin (`/admin/`) con un utente superuser, per le
      azioni "Conferma morti selezionati" / "Revoca conferma".

---

## Desktop (Chrome / Firefox / Edge)

### Autenticazione
- [ ] Signup con email/password: l'utente riceve l'email di verifica (se
      `ACCOUNT_EMAIL_VERIFICATION` non è `none`) e il link la conferma.
- [ ] Login con credenziali corrette e con credenziali errate (messaggio di
      errore chiaro, nessun redirect anomalo).
- [ ] Login via OAuth (Google e/o GitHub, se configurati in staging):
      redirect al provider, ritorno all'app, profilo utente creato con
      email corretta.
- [ ] Utente anonimo: qualunque URL diverso da login/signup/reset/statici
      reindirizza al login (middleware `LoginRequiredEverywhereMiddleware`).

### Crea lega e regole
- [ ] Creazione lega: nome, date (`start_date`/`end_date`,
      `registration_opens/closes`), regole squadra (`max_captains`,
      `max_non_captains`, `jolly_enabled`), moltiplicatori (`base_points`,
      `captain_multiplier`, `jolly_multiplier`), visibilità
      pubblica/privata.
- [ ] Configurazione bonus (`LeagueBonus`) dal pannello admin di lega
      (`/leghe/<slug>/admin/`), non dal Django admin.
- [ ] Validazioni date: `registration_closes` dopo `registration_opens`,
      `end_date` dopo `start_date` (messaggi di errore leggibili).

### Invito e iscrizione (lega privata)
- [ ] Generazione/copia del link invito con `invite_code` dal pannello
      admin lega.
- [ ] Un secondo utente si iscrive alla lega privata tramite il link o il
      codice invito.
- [ ] Rigenerazione dell'invito: il vecchio link/codice smette di
      funzionare, il nuovo funziona.
- [ ] Iscrizione a una lega pubblica senza codice invito.
- [ ] Iscrizione bloccata fuori dalla finestra `registration_opens/closes`.

### Crea squadra e cerca personaggi
- [ ] Creazione squadra nella lega (una squadra per utente per lega:
      verificare che una seconda creazione nella stessa lega sia bloccata).
- [ ] Ricerca persona (`/api/search-person/`) con filtro lingua della lega
      (`search_wikipedia_langs`): risultati coerenti, debounce percepibile,
      errori di rete mostrati inline (niente `alert()`).
- [ ] Aggiunta di tutti i morituri richiesti (`max_non_captains` + capitani,
      es. 11 + 1) fino al limite configurato; tentativo di superare il
      limite bloccato con messaggio chiaro.
- [ ] Impostazione capitano/i (rispetto di `max_captains`).
- [ ] Impostazione mese jolly (se `jolly_enabled`).
- [ ] Modal dettagli persona (click sul nome) apre i dati corretti senza
      navigare via dalla pagina.
- [ ] Blocco squadra (`is_locked`): dopo il lock, tentativi di modifica
      falliscono con messaggio esplicito.

### Simula decesso e verifica punteggio
- [ ] Dal Django admin, azione "Conferma morti selezionati" su un `Death`
      esistente (o creazione + conferma) per un morituro presente in
      almeno una squadra.
- [ ] Il punteggio in classifica lega (`/leghe/<slug>/classifica/`) si
      aggiorna con base + bonus + moltiplicatori corretti.
- [ ] La pagina decessi lega (`/leghe/<slug>/decessi/`) mostra il nuovo
      decesso con bonus applicati.
- [ ] Notifica **push** ricevuta dagli utenti con il morituro in squadra
      (urgente) e dagli altri membri della lega (normale), se hanno
      un abbonamento push attivo.
- [ ] Notifica **email** ricevuta da chi ha attivo il canale email per i
      decessi (matrice `notification_prefs`), non ricevuta da chi l'ha
      disattivato; oggetto "urgent" se il morituro era nella squadra del
      destinatario.
- [ ] Notifica nel **feed in-app** (`/notifiche/`) creata **sempre** per ogni
      membro della lega, a prescindere dai canali push/email; badge campanella
      incrementato; aprendo il feed il badge si azzera.
- [ ] Azione admin "Revoca conferma morti selezionati": il decesso torna
      non confermato, nessuna nuova notifica viene inviata, il punteggio si
      aggiorna di conseguenza.
- [ ] `data_frozen=True` su una `WikipediaPerson`: verificato che
      `check_deaths` (anche in staging, se eseguibile) la escluda, tranne
      con `--force`.

### Sostituzione entro deadline
- [ ] Dopo un decesso confermato, il countdown (`data-fm-countdown`) mostra
      il tempo residuo fino a `substitution_deadline_days`.
- [ ] Sostituzione del morituro deceduto con un altro personaggio prima
      della scadenza: la squadra si aggiorna, il vecchio membro resta in
      catena (`replaced_by`) come storico.
- [ ] Reminder di sostituzione (`send_substitution_reminders`) alle soglie
      T-3 e T-1: eseguire il comando in staging e verificare push/email
      ricevute una sola volta per soglia (no duplicati a run successive).
- [ ] Scadenza superata senza sostituzione: comportamento coerente con le
      regole della lega (blocco squadra / nessun effetto collaterale).

### Pannello admin lega
- [ ] Refresh Wikidata: diff (`/api/leghe/<slug>/wikidata-diff/`) mostra le
      differenze tra dati cache e Wikidata per i giocatori della lega.
- [ ] Applica modifiche (`/api/leghe/<slug>/wikidata-apply/`): i campi
      selezionati vengono aggiornati sul DB, gli altri restano invariati.
- [ ] Gestione membri: promozione/rimozione ruoli (`owner|admin|member`),
      rimozione di un membro dalla lega.
- [ ] Rigenerazione codice invito (vedi anche sezione invito sopra).
- [ ] Accesso al pannello admin negato a un utente `member` non admin.

### Profilo utente
- [ ] Cambio tema (`auto|light|dark`) da `/profilo/`: applicato subito e
      **salvato da solo** (autosave, niente pulsante "Salva"), persistito al
      refresh.
- [ ] Interruttore **push su questo dispositivo**: attivandolo il permesso viene
      richiesto e il dispositivo iscritto **senza refresh**; la riga "N
      dispositivi" si aggiorna subito; disattivandolo il dispositivo viene
      rimosso. "Invia test" recapita una push.
- [ ] **Matrice canali** (categoria × push/email): ogni toggle fa autosave; i
      valori sono rispettati dai flussi di notifica testati sopra; la colonna
      "In-app" è sempre attiva (non modificabile).

### PWA
- [ ] Install prompt PWA su Chrome/Edge desktop, icona e nome app corretti.
- [ ] Manifest (`/manifest.webmanifest`) valido (nome, icone, colori).
- [ ] Pagina offline (`/offline/`) mostrata quando la rete cade e la
      risorsa richiesta non è in cache.
- [ ] Dopo un deploy con `SW_CACHE_VERSION` cambiato: il nuovo service
      worker si installa e gli asset statici si aggiornano (niente asset
      "stale" serviti dalla cache vecchia).

### Reset password
- [ ] Richiesta reset password con email reale (SMTP di staging): email
      ricevuta con link valido.
- [ ] Link di reset porta al form, nuova password impostata, login con la
      nuova password funziona.
- [ ] Link di reset scaduto/già usato: messaggio di errore chiaro.

### Dark mode
- [ ] Nessun flash del tema sbagliato al caricamento (`data-theme` applicato
      inline prima del render) su: home, lista leghe, dettaglio lega,
      classifica, dettaglio squadra, edit squadra, dettaglio persona,
      dettaglio decesso, profilo, pannello admin lega.
- [ ] Contrasto leggibile in dark mode su tabelle, badge, modal, form.

---

## Android / Chrome

- [ ] Signup, login (incluso OAuth se configurato) via browser mobile.
- [ ] Flusso completo lega → squadra → aggiunta 11+1 personaggi → capitano/
      jolly, con tastiera touch e autocomplete ricerca persona.
- [ ] Notifiche **push** ricevute con schermo spento/app in background dopo
      un decesso confermato (richiede il permesso "Notifiche" concesso).
- [ ] Prompt "Installa app" / "Aggiungi a schermata Home" di Chrome:
      installazione, icona e splash screen corretti, apertura in modalità
      standalone (senza barra URL del browser).
- [ ] Pagina offline mostrata disattivando la connessione dati/Wi-Fi.
- [ ] Aggiornamento service worker dopo deploy con `SW_CACHE_VERSION`
      diverso (chiudere e riaprire l'app installata).
- [ ] Dark mode coerente con il tema di sistema se `theme_preference=auto`.
- [ ] **Viewport mobile (~375px di larghezza, es. Chrome DevTools o
      dispositivo reale)**: tabelle classifica/decessi scrollabili
      orizzontalmente senza rompere il layout, form di creazione
      lega/squadra utilizzabili senza campi tagliati, bottoni con area di
      tocco adeguata (niente doppio-tap accidentale su azioni distruttive
      come "Revoca conferma" o "Rimuovi membro").

## iOS / Safari

- [ ] Signup, login (incluso OAuth se configurato) via Safari mobile.
- [ ] Flusso completo lega → squadra → aggiunta personaggi → capitano/jolly.
- [ ] Istruzioni "Aggiungi a Home" mostrate correttamente (Safari non ha un
      prompt di installazione automatico come Chrome: verificare che l'app
      suggerisca esplicitamente il percorso manuale Condividi → Aggiungi a
      Home).
- [ ] App installata da Home Screen si apre in modalità standalone, con
      icona e nome corretti.
- [ ] Notifiche push: verificare il comportamento in base alla versione
      iOS/Safari disponibile in staging (il Web Push su iOS richiede l'app
      installata su Home Screen e una versione iOS recente); se non
      supportato, verificare che il toggle notifiche in `/profilo/` non
      rompa nulla e comunichi chiaramente il limite.
- [ ] Pagina offline mostrata in modalità aereo.
- [ ] Aggiornamento service worker dopo deploy con `SW_CACHE_VERSION`
      diverso (chiudere del tutto l'app da Home Screen e riaprirla).
- [ ] Dark mode coerente con le impostazioni di sistema se
      `theme_preference=auto`; toggle manuale funzionante.
- [ ] **Viewport mobile (~375px, iPhone SE/standard)**: stesso set di
      verifiche di Android — tabelle scrollabili, form leggibili senza
      zoom involontario (dimensione font minima negli input), bottoni con
      area di tocco adeguata, modal persona utilizzabile a schermo intero
      su schermi piccoli.

---

## Note finali

- Ripetere almeno il flusso "simula decesso → punti/push/email" dopo ogni
  modifica a `game/scoring.py`, `game/signals.py`, `game/push.py` o
  `game/email.py`.
- Ripetere la sezione PWA dopo ogni modifica al service worker
  (`templates/game/sw.js`) o al manifest.
- Segnare la data e l'ambiente (staging/produzione, commit/tag) in cui la
  checklist è stata eseguita, per tracciabilità pre-rilascio.
