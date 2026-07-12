# Changelog

Tutte le modifiche rilevanti di questo progetto sono documentate qui.
Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/),
il versionamento segue [SemVer](https://semver.org/lang/it/).

## [0.2.1] - 2026-07-12

- Gestisci i decessi pre-stagione come rimozione, non sostituzione
- `emit_league_lifecycle` agganciato al ciclo giornaliero dello scheduler:
  le notifiche feed di inizio/fine lega ora vengono generate davvero in
  produzione (prima il comando esisteva ma non veniva mai eseguito)

## [0.2.0] - 2026-07-12

- Migrazione completa da Bootstrap a un design system CSS custom, dark-first
  ("Notturno") con variante chiara ("Osso")
- Shell mobile-first: bottom nav a 4 tab, top bar senza hamburger
- Pannello admin e pagine membro mobile-first (bonus a card, iscritti/tabelle
  convertiti in liste)
- UI di gioco identica per tutti: rimosso l'override staff sulle squadre
- Gestione date squadre, danger zone a tab, poteri staff e restyling header
- Iscrizione/registrazioni rese coerenti ("Segui" vs creazione squadra)
- Notifiche: feed in-app + gestione push/preferenze ripensata (matrice
  categoria × canale)
- PWA: badge notifiche monocromatico, icone maskable, logo SVG inline

## [0.1.9] - 2026-07-10

- Fix `generate_vapid_keys`: chiave privata in DER base64url, non PEM
  (pywebpush si aspetta il DER nudo, non un PEM con header/footer)

## [0.1.8] - 2026-07-10

- Fix cache stale (service worker su API, rankings, bonus potenziali) e
  pulizia UI mobile
- Core unico di sync persona da Wikidata (`game/person_sync.py`), rimosso
  l'endpoint di apply selettivo

## [0.1.7] - 2026-07-09

- Bonus automatici di lega inclusi nel simulatore what-if
- Il controllo giocatori rinfresca anche `claims_cache` e le cache bonus
  (caso reale: Mario Monti senatore a vita)

## [0.1.6] - 2026-07-09

- Fix valore Wikidata del bonus Senatore a vita (P39)
- Rimossi i bonus di sistema manuali e "Campione olimpico" (detection P166
  troppo inaffidabile)

## [0.1.5] - 2026-07-09

- Fix cache-busting degli static in produzione (CSS vecchio servito con
  template nuovi)

## [0.1.4] - 2026-07-09

- UX: navbar fissa, regolamento e pagina lega ridisegnati, rimozione
  giocatori, bonus potenziali nel modal

## [0.1.3] - 2026-07-09

- Regolamento generico, riepilogo punteggi per lega e bonus manuali
- Danger zone per lega e squadra, breadcrumb di navigazione

## [0.1.2] - 2026-07-08

- Fix navbar fissa, form squadra unico e cancellazione squadra
- `ACCOUNT_SIGNUP_ENABLED` diviso in due flag indipendenti, classico e OAuth

## [0.1.1] - 2026-07-08

- Aggiunto interruttore via env per chiudere le nuove registrazioni (form e
  OAuth)

## [0.1.0] - 2026-07-08

- Prima release taggata: client Wikidata riscritto (sessione condivisa,
  retry, throttle e timeout)
