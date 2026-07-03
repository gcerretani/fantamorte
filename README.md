# Fantamorte

Web app Django per giocare al "fantacalcio dei decessi": ogni manager
compone una squadra di personaggi pubblici e prende punti quando uno di
loro muore durante il periodo di gioco. Il gioco è organizzato in **leghe**
indipendenti, ognuna con regole, calendario e bonus configurabili; i dati
biografici arrivano da Wikidata/Wikipedia.

Per l'architettura, i modelli, le convenzioni di codice e le note operative
dettagliate vedi [`CLAUDE.md`](CLAUDE.md).

## Setup sviluppo locale

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # poi compila i valori (per SQLite bastano i default)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Nota: `mysqlclient` richiede le librerie di sviluppo MariaDB/MySQL installate
a livello di sistema (es. `libmariadb-dev` su Debian/Ubuntu, `mariadb-connector-c`
su macOS). Per lo sviluppo locale con SQLite (default se `DATABASE_URL` non è
impostata) questo passaggio non serve: si può anche saltare l'installazione
di `mysqlclient` se dà problemi, a patto di non toccare il codice che dipende
dal driver MySQL.

Per abilitare le notifiche push (facoltativo in sviluppo):

```bash
python manage.py generate_vapid_keys
```

## Deploy in produzione (Docker Compose)

`docker-compose.yml` descrive lo stack completo: `db` (MariaDB), `web`
(Gunicorn dietro `entrypoint.sh`, che applica le migration all'avvio),
`nginx` + `certbot` (reverse proxy con TLS Let's Encrypt davanti a `web`) e
`scheduler` (job periodici di gioco). Variabili obbligatorie (vedi
`.env.example` per l'elenco completo):

- **`SECRET_KEY`**: obbligatoria quando `DEBUG=False` (default) — senza,
  l'app si rifiuta di avviarsi.
- **`ALLOWED_HOSTS`**: domini/IP autorizzati, separati da virgola.
- **`DATABASE_URL`** (o le variabili `DB_PASSWORD`/`DB_ROOT_PASSWORD` usate
  dal compose per MariaDB): stringa di connessione al database.
- **`SERVER_NAME`**: dominio pubblico servito da `nginx`, usato anche per
  richiedere il certificato Let's Encrypt via `certbot`.
- **`EMAIL_*`** (backend SMTP, host, credenziali, `DEFAULT_FROM_EMAIL`):
  **senza un backend SMTP reale configurato, il reset password e le altre
  email transazionali (notifica decesso, reminder sostituzione) non vengono
  mai recapitate.** Il default è il backend console, adatto solo allo
  sviluppo.
- **`VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` / `VAPID_CLAIM_EMAIL`**: per le
  Web Push. Genera la coppia di chiavi con `python manage.py generate_vapid_keys`.
  Senza VAPID configurato, i tentativi di push sono no-op (non bloccano nulla).

Altri parametri opzionali utili in produzione: `SW_CACHE_VERSION` (da
cambiare a ogni deploy per invalidare la cache del service worker lato
client), le credenziali OAuth (`GOOGLE_OAUTH_*`, `GITHUB_OAUTH_*`, opzionali:
se vuote i relativi pulsanti di login social spariscono), gli header HSTS,
`SCHEDULER_DAILY_HOUR`/`SCHEDULER_INTERVAL_SECONDS` (frequenza dei job del
servizio `scheduler`: i reminder sono giornalieri, `check_deaths` gira a
intervallo) e `BACKUP_KEEP_DAYS` (rotazione dei backup del servizio
`db_backup`).

Il servizio `scheduler` è quello che esegue periodicamente i job di gioco
(`check_deaths`, `send_substitution_reminders`): senza, i decessi non
vengono mai rilevati e i reminder non partono. I bonus primo/ultimo morto
sono calcolati dinamicamente dallo scoring, per lega, senza job dedicati.

```bash
docker compose up -d
docker compose exec web python manage.py migrate

# Emissione iniziale del certificato TLS (una tantum, poi certbot rinnova da solo):
docker compose run --rm certbot certonly --webroot -w /var/www/certbot \
  -d $SERVER_NAME --email <email> --agree-tos --no-eff-email
docker compose restart nginx
```

## Comandi di manutenzione

Le management command lavorano per **lega** (argomento `--league <slug>`),
non per stagione; in mancanza dell'argomento operano sulle leghe in corso.

- `check_deaths` — interroga Wikidata e registra i decessi dei morituri
  delle leghe in corso (auto-conferma se la data di morte è valida; usa
  `--dry-run` per una simulazione, `--force` per ignorare `data_frozen` e
  l'intervallo di ricontrollo, `--no-autoconfirm` per creare i decessi non
  confermati).
- `mark_originals` — segna i membri "originali" di una squadra a inizio
  stagione, abilitando il bonus corrispondente.
- `send_substitution_reminders` — invia push/email di promemoria (soglie
  T-3 e T-1 giorni) per le sostituzioni in scadenza; `--dry-run` per loggare
  senza inviare.
- `generate_vapid_keys` — genera la coppia di chiavi VAPID per le Web Push.

## Test

```bash
python manage.py test game wikidata_api
```
