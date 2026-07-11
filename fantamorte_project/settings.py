import time
from pathlib import Path
import environ
from django.contrib.messages import constants as messages_constants

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / '.env', overwrite=False)

DEBUG = env('DEBUG')

# In produzione (DEBUG=False) la SECRET_KEY è obbligatoria: nessun default,
# l'app deve fallire subito all'avvio se manca invece di partire insicura.
if DEBUG:
    SECRET_KEY = env('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
else:
    SECRET_KEY = env('SECRET_KEY')

# In sviluppo (DEBUG=True) va bene un default comodo; in produzione
# ALLOWED_HOSTS va sempre configurato esplicitamente via env.
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1'] if DEBUG else [])

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    # OAuth/social auth tramite django-allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.github',
    'game',
    'wikidata_api',
]

SITE_ID = env.int('SITE_ID', default=1)

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # GZip va dopo Security e prima di tutto il resto. Comprime risposte HTML/JSON.
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # allauth richiede questo middleware
    'allauth.account.middleware.AccountMiddleware',
    'game.middleware.LoginRequiredEverywhereMiddleware',
]

# --- Sicurezza in produzione ---
# Il redirect HTTP -> HTTPS è delegato al reverse proxy (nginx), qui solo
# gli header/cookie di sicurezza che dipendono da una connessione già HTTPS.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = env.bool('SECURE_HSTS_PRELOAD', default=False)

# I messaggi Django sono resi come alert-<tag>: i tag di default 'error' e
# 'debug' non hanno una variante alert dedicata, li rimappiamo su danger/secondary.
MESSAGE_TAGS = {
    messages_constants.ERROR: 'danger',
    messages_constants.DEBUG: 'secondary',
}

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

ROOT_URLCONF = 'fantamorte_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'fantamorte_project.wsgi.application'

DATABASES = {
    'default': env.db(
        'DATABASE_URL',
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}'
    )
}
# Persistent connections (no-op su SQLite; utile su MariaDB/Postgres in produzione).
DATABASES['default']['CONN_MAX_AGE'] = env.int('CONN_MAX_AGE', default=60)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'it-it'
TIME_ZONE = 'Europe/Rome'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# In produzione usa il manifest storage per cache busting automatico (hash nei
# nomi file). In sviluppo si tiene il default per evitare overhead di collectstatic.
# NB: va configurato via STORAGES (Django >= 4.2): la vecchia impostazione
# STATICFILES_STORAGE è stata rimossa in Django 5.1 e verrebbe ignorata in
# silenzio, lasciando i file senza hash (e nginx li serve con cache 30 giorni).
if not DEBUG:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage',
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# --- django-allauth ---
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = env('ACCOUNT_EMAIL_VERIFICATION', default='mandatory')
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = env('ACCOUNT_DEFAULT_HTTP_PROTOCOL', default='https')
# Widget con le classi del design system applicate server-side (game/forms.py).
ACCOUNT_FORMS = {
    'login': 'game.forms.LoginForm',
    'signup': 'game.forms.SignupForm',
    'reset_password': 'game.forms.ResetPasswordForm',
    'reset_password_from_key': 'game.forms.ResetPasswordKeyForm',
    'change_password': 'game.forms.ChangePasswordForm',
}
ACCOUNT_ADAPTER = 'game.adapters.ClosedSignupAccountAdapter'
SOCIALACCOUNT_ADAPTER = 'game.adapters.ClosedSignupSocialAccountAdapter'
# Due interruttori indipendenti per le nuove registrazioni. Nessuno dei due
# tocca il login di chi ha gia' un account (form o OAuth che sia).
# A False chiude il signup via form email+password.
ACCOUNT_SIGNUP_ENABLED = env.bool('ACCOUNT_SIGNUP_ENABLED', default=True)
# A False chiude la creazione automatica di un account al primo login OAuth.
SOCIALACCOUNT_SIGNUP_ENABLED = env.bool('SOCIALACCOUNT_SIGNUP_ENABLED', default=True)
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_AUTO_SIGNUP = True
# Un provider entra in SOCIALACCOUNT_PROVIDERS solo se le sue env sono
# valorizzate. Se la chiave manca, allauth non registra nessuna "app" da
# settings per quel provider, lasciando spazio a un SocialApp configurato
# da Django admin (/admin/socialaccount/socialapp/) senza conflitti:
# allauth unisce le app da DB e da settings, quindi un'app vuota qui
# duplicherebbe quella creata da admin e romperebbe il login
# (get_app() -> MultipleObjectsReturned). Le due modalità di
# configurazione (env vs admin) vanno usate in alternativa per lo stesso
# provider, non insieme.
SOCIALACCOUNT_PROVIDERS = {}
if env('GOOGLE_OAUTH_CLIENT_ID', default=''):
    SOCIALACCOUNT_PROVIDERS['google'] = {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'APP': {
            'client_id': env('GOOGLE_OAUTH_CLIENT_ID'),
            'secret': env('GOOGLE_OAUTH_CLIENT_SECRET', default=''),
            'key': '',
        },
    }
if env('GITHUB_OAUTH_CLIENT_ID', default=''):
    SOCIALACCOUNT_PROVIDERS['github'] = {
        'SCOPE': ['user:email'],
        'APP': {
            'client_id': env('GITHUB_OAUTH_CLIENT_ID'),
            'secret': env('GITHUB_OAUTH_CLIENT_SECRET', default=''),
            'key': '',
        },
    }

# --- Email ---
EMAIL_BACKEND = env(
    'EMAIL_BACKEND',
    default='django.core.mail.backends.console.EmailBackend',
)
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='Fantamorte <noreply@fantamorte.local>')
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)

# --- Web Push (VAPID) ---
VAPID_PUBLIC_KEY = env('VAPID_PUBLIC_KEY', default='')
VAPID_PRIVATE_KEY = env('VAPID_PRIVATE_KEY', default='')
VAPID_CLAIM_EMAIL = env('VAPID_CLAIM_EMAIL', default='admin@fantamorte.local')

# --- PWA ---
PWA_APP_NAME = 'Fantamorte'
PWA_APP_SHORT_NAME = 'Fantamorte'
# Palette «Notturno»: tenere in sync con i token --fm-* in
# static/css/fantamorte.css (theme = top bar, background = fondo pagina dark).
PWA_APP_THEME_COLOR = '#171a20'
PWA_APP_BACKGROUND_COLOR = '#111318'

WIKIDATA_USER_AGENT = env('WIKIDATA_USER_AGENT', default='Fantamorte/1.0 (fantamorte@example.com)')
WIKIDATA_REQUEST_DELAY = env.float('WIKIDATA_REQUEST_DELAY', default=0.5)

# --- Cache ---
# In produzione il compose imposta REDIS_URL: cache condivisa tra i worker
# Gunicorn e lo scheduler (classifiche, ricerca Wikidata, check bonus).
# Senza REDIS_URL (sviluppo/test) Django usa la LocMemCache per-processo.
REDIS_URL = env('REDIS_URL', default='')
if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': REDIS_URL,
            'KEY_PREFIX': 'fantamorte',
        }
    }

# Mostra il pulsante VAPID disponibile al template
TEMPLATES[0]['OPTIONS']['context_processors'].append('game.context_processors.public_settings')

# --- Logging ---
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': env('DJANGO_LOG_LEVEL', default='INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'game': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'wikidata_api': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# Versione della cache del service worker: cambia ad ogni deploy per
# invalidare la cache lato client (vedi templates/game/sw.js). Se non
# impostata via env (es. hash del commit), usa il timestamp di avvio.
SW_CACHE_VERSION = env('SW_CACHE_VERSION', default='') or str(int(time.time()))
