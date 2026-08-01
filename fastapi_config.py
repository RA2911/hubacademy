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


SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', 'http://127.0.0.1:8000')
DEFAULT_CURRENCY = os.environ.get('DEFAULT_CURRENCY', 'USD').upper()

R2_ACCOUNT_ID = os.environ.get('R2_ACCOUNT_ID', '')
R2_ACCESS_KEY_ID = os.environ.get('R2_ACCESS_KEY_ID', '')
R2_SECRET_ACCESS_KEY = os.environ.get('R2_SECRET_ACCESS_KEY', '')
R2_BUCKET = os.environ.get('R2_BUCKET', '')
R2_PUBLIC_BASE_URL = os.environ.get('R2_PUBLIC_BASE_URL', '')
R2_PRESIGN_EXPIRES_SECONDS = int(os.environ.get('R2_PRESIGN_EXPIRES_SECONDS', '3600'))

STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET', '')
STRIPE_MONTHLY_PRICE_ID = os.environ.get('STRIPE_MONTHLY_PRICE_ID', '')
STRIPE_ANNUAL_PRICE_ID = os.environ.get('STRIPE_ANNUAL_PRICE_ID', '')
SUBSCRIPTION_MONTHLY_PRICE = os.environ.get('SUBSCRIPTION_MONTHLY_PRICE', '29')
SUBSCRIPTION_ANNUAL_PRICE = os.environ.get('SUBSCRIPTION_ANNUAL_PRICE', '249')

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
