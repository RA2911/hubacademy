import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def database_url():
    url = os.environ.get('DATABASE_URL')
    if not url:
        return 'sqlite:///' + os.path.join(BASE_DIR, 'hub_academy_elearning.db')
    if url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql+psycopg2://', 1)
    if url.startswith('postgresql://'):
        return url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    return url


def _clean_secret(name, default=''):
    """Read an env var and strip ALL whitespace/newlines. API keys, price IDs
    and URLs never contain internal spaces, so this safely repairs values that
    picked up a stray line break when pasted (e.g. into a deploy command),
    which would otherwise make an HTTP header illegal and crash every request."""
    return ''.join(os.environ.get(name, default).split())


SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
PUBLIC_BASE_URL = _clean_secret('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
DEFAULT_CURRENCY = os.environ.get('DEFAULT_CURRENCY', 'USD').upper()

R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID', '')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY', '')
R2_BUCKET = os.environ.get('R2_BUCKET', '')
R2_PUBLIC_BASE_URL = os.environ.get('R2_PUBLIC_BASE_URL', '')
R2_PRESIGN_EXPIRES_SECONDS = int(os.environ.get('R2_PRESIGN_EXPIRES_SECONDS', '3600'))

# Shared secret the external AI course-generator app uses to deliver finished
# courses into this platform (see POST /api/factory/deliver).
FACTORY_API_KEY = os.environ.get('FACTORY_API_KEY', '')

STRIPE_SECRET_KEY = _clean_secret('STRIPE_SECRET_KEY')
STRIPE_WEBHOOK_SECRET = _clean_secret('STRIPE_WEBHOOK_SECRET')
STRIPE_MONTHLY_PRICE_ID = _clean_secret('STRIPE_MONTHLY_PRICE_ID')
STRIPE_ANNUAL_PRICE_ID = _clean_secret('STRIPE_ANNUAL_PRICE_ID')
SUBSCRIPTION_MONTHLY_PRICE = os.environ.get('SUBSCRIPTION_MONTHLY_PRICE', '39')
SUBSCRIPTION_ANNUAL_PRICE = os.environ.get('SUBSCRIPTION_ANNUAL_PRICE', '249')
# How many courses a monthly subscriber can have unlocked at once. 0 = unlimited.
SUBSCRIPTION_COURSE_LIMIT = int(os.environ.get('SUBSCRIPTION_COURSE_LIMIT', '0'))

# Private analytics. Local MaxMind GeoLite2 country DB (optional — country shows
# "Unknown" until the file is present). Page views/events auto-prune after N days.
GEOIP_DB_PATH = os.environ.get('GEOIP_DB_PATH', os.path.join(BASE_DIR, 'GeoLite2-Country.mmdb'))
ANALYTICS_RETENTION_DAYS = int(os.environ.get('ANALYTICS_RETENTION_DAYS', '90'))
# Weekly emailed PDF report: secret that protects the /tasks/weekly-report URL,
# and the recipient address.
REPORT_KEY = _clean_secret('REPORT_KEY')
REPORT_EMAIL = os.environ.get('REPORT_EMAIL', 'support@hubacademy.ai')

# Google Sign-In (OAuth). When set, a "Continue with Google" button appears on the
# login/register pages and POSTs the Google credential to /auth/google. Dormant
# until this client ID is configured (same pattern as the dormant annual plan).
GOOGLE_OAUTH_CLIENT_ID = _clean_secret('GOOGLE_OAUTH_CLIENT_ID')

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
OPENAI_AUDIO_MODEL = os.environ.get('OPENAI_AUDIO_MODEL', 'gpt-4o-mini-tts')
OPENAI_VOICE = os.environ.get('OPENAI_VOICE', 'alloy')

DID_API_KEY = os.environ.get('DID_API_KEY', '')
DID_SOURCE_IMAGE_URL = os.environ.get('DID_SOURCE_IMAGE_URL', '')

MAIL_SERVER = os.environ.get('MAIL_SERVER', '')
MAIL_PORT = int(os.environ.get('MAIL_PORT', '587'))
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ('1', 'true', 'yes', 'on')
MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() in ('1', 'true', 'yes', 'on')
MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', MAIL_USERNAME)


def validate_production():
    if os.environ.get('APP_ENV') == 'production' or os.environ.get('ENV') == 'production':
        if SECRET_KEY == 'dev-secret-key-change-in-production':
            raise RuntimeError('Set SECRET_KEY before running in production.')
