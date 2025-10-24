import os
from pathlib import Path
import dj_database_url 
import sys 

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# --- 1. CORE PRODUCTION SETTINGS ---
# IMPORTANT: Read secrets from Render environment variables 
SECRET_KEY = os.environ.get('SECRET_KEY', 'django-insecure-=+c$c$j4z!0d9v$j1w!5a)0i=d!o(l!&!1v(l3x(e&n&n7z_d3')

# Set DEBUG=False for production! Use environment variable to control it
DEBUG = 'RENDER' not in os.environ 

# Render domain and localhost must be allowed
ALLOWED_HOSTS = [os.environ.get('RENDER_EXTERNAL_HOSTNAME', '127.0.0.1'), 'localhost', '127.0.0.1']

INSTALLED_APPS = [
    # NEW: WhiteNoise must be first for development static serving
    'whitenoise.runserver_nostatic', 
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_q',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'api',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # NEW: WhiteNoise middleware for serving static files efficiently
    'whitenoise.middleware.WhiteNoiseMiddleware', 
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'clinic_token_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
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

WSGI_APPLICATION = 'clinic_token_system.wsgi.application'


# --- 2. DATABASE CONFIGURATION (PostgreSQL for Render) ---
DATABASES = {
    'default': dj_database_url.config(
        # Read DATABASE_URL from Render environment variable (for prod)
        default=os.environ.get('DATABASE_URL', 'sqlite:///./db.sqlite3'),
        conn_max_age=600,
        # --- FIX: Renamed check to checks ---
        conn_health_checks=True, 
    )
}

if 'RENDER' not in os.environ and 'test' not in sys.argv:
    DATABASES['default']['ENGINE'] = 'django.db.backends.sqlite3'


AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
    ],
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata' 
USE_I18N = True
USE_TZ = True

# --- 3. STATIC FILES CONFIGURATION (for WhiteNoise) ---
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles') # Render will look here
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- CORS Configuration ---
# NOTE: Removed the environment reading that caused the earlier migration error
CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    # Render will inject the live Netlify/Frontend URL here via ENV variable
    os.environ.get("CORS_FRONTEND_URL", ""),
]
CORS_ALLOW_CREDENTIALS = True

# --- 4. SMS/IVR CONFIGURATION (Dummy Keys for Simulation) ---
# NOTE: These variables are kept for code consistency but are ignored by api/utils.py
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', 'ACXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', 'your_auth_token')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '+15005550006')

# --- 5. DJANGO-Q SETTINGS (Free Tier Only Runs Web) ---
Q_CLUSTER = {
    'name': 'clinic-q-local',
    'workers': 4,
    'timeout': 90,
    'retry': 120,
    'queue_limit': 50,
    'catch_up': False,
    'redis': os.environ.get('REDIS_URL', 'redis://localhost:6379/0') 
}
