from pathlib import Path
import os
import sys
import dj_database_url
from dotenv import load_dotenv

# Test ishga tushirilyaptimi. Django test runner DEBUG ni majburan
# False qiladi, shuning uchun "ishlab turgan server" sozlamalari
# testlarda ham yoqilib ketadi — ba'zilari test mijoziga to'g'ri
# kelmaydi (masalan https ga yo'naltirish).
TESTING = "test" in sys.argv or "pytest" in sys.modules

BASE_DIR = Path(__file__).resolve().parent.parent

# Lokal ishlab chiqishda BASE_DIR/.env dan o'qiladi (Render'da env dashboard'dan keladi)
load_dotenv(BASE_DIR / ".env")


# SECURITY
_INSECURE_KEY = "django-insecure-change-this-key"
SECRET_KEY = os.environ.get("SECRET_KEY", _INSECURE_KEY)

# Read DEBUG from environment for deploy flexibility
DEBUG = os.environ.get("DEBUG", "False").lower() in ("1", "true", "yes")

# SECRET_KEY sozlanmagan bo'lsa hamma kodda yozilgan standart kalitni
# ishlatadi — u ochiq repoda turgani uchun begona odam imzolangan
# qiymatlarni (sessiya, CSRF) o'zi yasay oladi. Telegram webhook siri
# ham shu kalitdan hosil qilinadi, ya'ni u ham taxmin qilinadigan
# bo'lib qoladi. Bu yerda xato ko'tarilmaydi (sayt to'xtab qolmasin),
# lekin Render loglarida ko'rinib turadi.
if not DEBUG and SECRET_KEY == _INSECURE_KEY:
    import logging as _logging

    _logging.getLogger(__name__).error(
        "XAVFSIZLIK: SECRET_KEY muhit o'zgaruvchisi sozlanmagan — "
        "kodga yozilgan standart kalit ishlatilyapti. Render'da "
        "SECRET_KEY ni o'rnating."
    )

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

# ─────────────────────────────
# XAVFSIZLIK SARLAVHALARI
# Faqat ishlab turgan serverda — lokalda https bo'lmagani uchun
# yoqilsa brauzer sahifani umuman ocha olmaydi.
# ─────────────────────────────
if not DEBUG:
    # Cookie'lar faqat https orqali yuborilsin. Bu loyihada kirish
    # cookie'ga tayanmaydi (sarlavha + localStorage), shuning uchun
    # ta'sir kichik — lekin admin paneli sessiyasi shular bilan yuradi.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    # http bilan kelgan so'rov https ga yo'naltiriladi. Render TLS ni
    # o'zi tugatib X-Forwarded-Proto yuboradi, yuqoridagi
    # SECURE_PROXY_SSL_HEADER shuni o'qiydi — shuning uchun cheksiz
    # yo'naltirish halqasi bo'lmaydi. Kutilmagan holat chiqsa
    # SECURE_SSL_REDIRECT=0 bilan o'chirib turish mumkin.
    #
    # Testlarda o'chiriladi: Django test mijozi http bilan yuradi va
    # DEBUG ni False qilib qo'yadi — yoqiq qolsa har bir so'rov 301
    # bo'lib, javob JSON o'rniga yo'naltirish sahifasi bo'lib qolardi.
    SECURE_SSL_REDIRECT = (not TESTING) and os.environ.get(
        "SECURE_SSL_REDIRECT", "True"
    ).lower() in ("1", "true", "yes")

    # Brauzer shu domenga faqat https bilan borsin. Muddat ataylab
    # qisqa (1 kun) va subdomenlarga tarqalmaydi: HSTS ni orqaga
    # qaytarish qiyin, shuning uchun avval qisqa muddat bilan
    # ishonch hosil qilinadi, keyin oshiriladi.
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "86400"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

    # Brauzer javobdagi Content-Type ni o'zi "taxmin qilib" o'zgartirmasin
    SECURE_CONTENT_TYPE_NOSNIFF = True

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
if FRONTEND_BASE_URL and FRONTEND_BASE_URL not in CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS.append(FRONTEND_BASE_URL)

# Vercel uchun umumiy subdomain moslama. Agar frontend boshqa Vercel
# loyihaga o'tsa, bu ham ishlaydi.
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://.*\.vercel\.app$"]

# ⚠️ Avval bu yerda shartsiz `CORS_ALLOW_ALL_ORIGINS = True` turardi
# ("test uchun" deb). Amalda bu istalgan begona saytga API javobini
# brauzerda o'qish imkonini berardi: foydalanuvchi shunday saytga
# kirsa, u o'quvchilar ro'yxati, telefon raqamlari va to'lovlarni
# to'liq o'qib olishi mumkin edi. Chaqiruvchi 'X-User-Phone' sarlavhasi
# bilan aniqlanadi va uni soxtalashtirish oson bo'lgani uchun bu
# ayniqsa xavfli.
#
# Endi faqat lokal ishlab chiqishda ochiq. Ishlab turgan serverda
# yuqoridagi ro'yxat va vercel.app shabloni yetarli — frontend
# o'shalardan biriga tushadi.
CORS_ALLOW_ALL_ORIGINS = DEBUG

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
    "PUBLIC_BASE_URL", "https://demo-django-c3eh.onrender.com"
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
