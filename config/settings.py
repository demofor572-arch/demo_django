from pathlib import Path
import os
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Lokal ishlab chiqishda BASE_DIR/.env dan o'qiladi (Render'da env dashboard'dan keladi)
load_dotenv(BASE_DIR / ".env")


# SECURITY
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-change-this-key")

# Read DEBUG from environment for deploy flexibility
DEBUG = os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes")

# Allow hosts configurable via env var; keep render domain by default
ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "127.0.0.1,localhost,.onrender.com",
).split(",")


# APPLICATIONS
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "register_withvue",
]


# MIDDLEWARE
MIDDLEWARE = [
    # JSON javoblarni siqish — katta ro'yxatlar (~10x kichikroq) tezroq yuklanadi
    "django.middleware.gzip.GZipMiddleware",
    "config.middleware.JsonExceptionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise static uchun
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


ROOT_URLCONF = "config.urls"


# TEMPLATES
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"


# DATABASE
# Fallback to a local sqlite DB when DATABASE_URL is not provided
sqlite_path = str(BASE_DIR / "db.sqlite3").replace("\\", "/")
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL"),
        conn_max_age=600,
    )
}


DATABASE_URL = os.environ.get("DATABASE_URL", "")

# If DATABASE_URL is set use dj_database_url to parse it, otherwise fall
# back to a local sqlite database as the comment above intended.
if isinstance(DATABASE_URL, bytes):
    DATABASE_URL = DATABASE_URL.decode()

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": sqlite_path,
        }
    }
# When behind a proxy (Render), honor X-Forwarded-Proto for secure URLs
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# PASSWORD VALIDATION
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# LANGUAGE
LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True
USE_TZ = True


# STATIC FILES
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# DEFAULT PRIMARY KEY
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS
FRONTEND_BASE_URL = os.environ.get(
    "FRONTEND_BASE_URL", "https://demo-bay-eta-38.vercel.app"
).rstrip("/")

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:8080",
    "https://demo-bay-eta-38.vercel.app",
]

# Vercel uchun umumiy subdomain moslama. Agar frontend boshqa Vercel
# loyihaga o'tsa, bu ham ishlaydi.
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://.*\.vercel\.app$"]

# test uchun va frontend URL'larini brauzerda sinov qilish uchun
CORS_ALLOW_ALL_ORIGINS = True

# Menejer paneli destruktiv amallarda 'X-User-Phone' sarlavhasini,
# har bir so'rovda esa 'X-Device-Id' ni yuboradi (supermenejer qaysi
# qurilmadan kirilganini shu orqali ko'radi). Standart bo'lmagan
# sarlavha CORS preflight'ni ishga tushiradi — ro'yxatga qo'shilmasa
# brauzer so'rovni bloklaydi va sahifada "Internet aloqasi yo'q"
# ko'rinadi. Yangi sarlavha qo'shsangiz, shu ro'yxatga ham qo'shing.
from corsheaders.defaults import default_headers  # noqa: E402

CORS_ALLOW_HEADERS = (*default_headers, "x-user-phone", "x-device-id")

# ─────────────────────────────
# TELEGRAM BOT (o'quvchilarga xabar yuborish)
# Tavsiya: tokenni Render'da TG_BOT_TOKEN env o'zgaruvchisiga ko'chiring
# ─────────────────────────────
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")

# Bot username (@siz) — frontend'ga "botga kiring" havolasini ko'rsatish uchun.
# Bo'sh qoldirilsa `set_webhook` buyrug'i chiqargan nomdan foydalaning.
TG_BOT_USERNAME = os.environ.get("TG_BOT_USERNAME", "itline_test_2026bot")

# Backend'ning tashqi manzili — webhook'ni ro'yxatdan o'tkazish uchun.
# Bu maxfiy emas (manzil baribir hammaga ko'rinadi), shuning uchun
# standart qiymat shu yerda turadi va env o'zgaruvchisi shart emas.
# Boshqa domenga ko'chsangiz PUBLIC_BASE_URL orqali almashtirasiz.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL", "https://itline-django-9s85.onrender.com"
).rstrip("/")

# Webhook maxfiy kaliti. Telegram har bir so'rovda buni
# 'X-Telegram-Bot-Api-Secret-Token' sarlavhasida qaytaradi — shu orqali
# soxta (begona) so'rovlarni rad etamiz.
#
# Alohida env o'zgaruvchisi shart emas: berilmasa SECRET_KEY'dan
# hosil qilinadi. SECRET_KEY allaqachon maxfiy va Render'da bor, ya'ni
# kalit ham maxfiy bo'ladi-yu, sozlaydigan narsa kamayadi. Telegram
# kalit uzunligini 1..256 belgi va faqat A-Z a-z 0-9 _ - deb cheklaydi,
# shuning uchun hex ishlatamiz.
TG_WEBHOOK_SECRET = os.environ.get("TG_WEBHOOK_SECRET", "")
if not TG_WEBHOOK_SECRET:
    import hashlib

    TG_WEBHOOK_SECRET = hashlib.sha256(
        f"tg-webhook:{SECRET_KEY}".encode()
    ).hexdigest()

# ─────────────────────────────
# ADMIN / MENEJER PANEL PAROLLARI
# Kodda saqlanmaydi — lokalda .env, deployda Render env orqali beriladi
# ─────────────────────────────
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
EXCELLENCE_PASSWORD = os.environ.get("EXCELLENCE_PASSWORD", "")
