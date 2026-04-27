from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, True))
environ.Env.read_env(BASE_DIR / '.env', overwrite=False)

SECRET_KEY = env('SECRET_KEY', default='django-insecure-dev-key-change-in-production')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['*'])

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
STATIC_ROOT = BASE_DIR / 'staticfiles'

# In produzione usa il manifest storage per cache busting automatico (hash nei
# nomi file). In sviluppo si tiene il default per evitare overhead di collectstatic.
if not DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

# --- django-allauth ---
ACCOUNT_LOGIN_METHODS = {'username', 'email'}
ACCOUNT_SIGNUP_FIELDS = ['email*', 'username*', 'password1*', 'password2*']
ACCOUNT_EMAIL_VERIFICATION = env('ACCOUNT_EMAIL_VERIFICATION', default='optional')
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = env('ACCOUNT_DEFAULT_HTTP_PROTOCOL', default='https')
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'APP': {
            'client_id': env('GOOGLE_OAUTH_CLIENT_ID', default=''),
            'secret': env('GOOGLE_OAUTH_CLIENT_SECRET', default=''),
            'key': '',
        },
    } if env('GOOGLE_OAUTH_CLIENT_ID', default='') else {'APP': {'client_id': '', 'secret': '', 'key': ''}},
    'github': {
        'SCOPE': ['user:email'],
        'APP': {
            'client_id': env('GITHUB_OAUTH_CLIENT_ID', default=''),
            'secret': env('GITHUB_OAUTH_CLIENT_SECRET', default=''),
            'key': '',
        },
    } if env('GITHUB_OAUTH_CLIENT_ID', default='') else {'APP': {'client_id': '', 'secret': '', 'key': ''}},
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
PUSH_NOTIFICATIONS_ASYNC = env.bool('PUSH_NOTIFICATIONS_ASYNC', default=False)

# --- PWA ---
PWA_APP_NAME = 'Fantamorte'
PWA_APP_SHORT_NAME = 'Fantamorte'
PWA_APP_THEME_COLOR = '#212529'
PWA_APP_BACKGROUND_COLOR = '#f8f9fa'

WIKIDATA_USER_AGENT = env('WIKIDATA_USER_AGENT', default='Fantamorte/1.0 (fantamorte@example.com)')
WIKIDATA_REQUEST_DELAY = env.float('WIKIDATA_REQUEST_DELAY', default=0.5)

# Mostra il pulsante VAPID disponibile al template
TEMPLATES[0]['OPTIONS']['context_processors'].append('game.context_processors.public_settings')
