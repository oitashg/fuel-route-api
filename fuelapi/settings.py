"""Django settings for the fuel-route API."""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_list(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-only-key-do-not-use-in-production",
)
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS") or (["*"] if DEBUG else [])
for _var in ("RENDER_EXTERNAL_HOSTNAME", "FLY_APP_NAME", "RAILWAY_PUBLIC_DOMAIN"):
    if os.environ.get(_var):
        ALLOWED_HOSTS.append(os.environ[_var])

CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "fuel",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# There is no auth or user data behind this API, so anything may call it.
CORS_ALLOWED_ORIGINS = _env_list("DJANGO_CORS_ALLOWED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = not CORS_ALLOWED_ORIGINS

ROOT_URLCONF = "fuelapi.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "fuelapi.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # The hashed manifest needs collectstatic to have run, which is not
        # true while developing or under the test runner.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}

# Routes are deterministic for a given pair of coordinates, so a repeat request
# is answered from cache and costs zero upstream calls.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route",
        "TIMEOUT": 60 * 60 * 24,
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
}

# --- Upstream map services ------------------------------------------------
# Both are free and keyless. Nominatim only ever sees the two addresses typed
# by the caller; every fuel stop is matched offline against the local table.
NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
OSRM_URL = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
GEO_USER_AGENT = os.environ.get("GEO_USER_AGENT", "fuel-route-api/1.0 (backend assessment)")
GEO_TIMEOUT = float(os.environ.get("GEO_TIMEOUT", "20"))

# Basemap for the map page. Note the {z}/{y}/{x} order, which is Esri's, not
# the {z}/{x}/{y} most tile servers use.
#
# OpenStreetMap's own tile servers are the obvious choice and the wrong one
# here: they run on donations and refuse traffic they do not recognise, serving
# an "Access blocked" image instead of a map. Carto's free tiles now watermark
# every tile with "API key required". Esri's are keyless, carry street labels,
# and only ask for attribution.
MAP_TILE_URL = os.environ.get(
    "MAP_TILE_URL",
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map"
    "/MapServer/tile/{z}/{y}/{x}",
)
MAP_TILE_ATTRIBUTION = os.environ.get(
    "MAP_TILE_ATTRIBUTION",
    'Tiles &copy; <a href="https://www.esri.com/">Esri</a>. Routing and '
    'geocoding from <a href="https://www.openstreetmap.org/copyright">'
    "OpenStreetMap</a> contributors.",
)

# --- Vehicle and planning parameters --------------------------------------
VEHICLE_MPG = float(os.environ.get("VEHICLE_MPG", "10"))
VEHICLE_RANGE_MI = float(os.environ.get("VEHICLE_RANGE_MI", "500"))

# What a stop is worth avoiding, in dollars: pulling off, fuelling and getting
# back on is a quarter of an hour of a driver's time. Used to choose the stops,
# never added to the fuel bill. 0 gives the cheapest plan regardless of stops.
STOP_PENALTY_USD = float(os.environ.get("STOP_PENALTY_USD", "10"))

# A truck stop counts as "on the route" if it sits within this many miles of
# the driven line. Widened once before giving up on a sparse corridor.
CORRIDOR_MI = float(os.environ.get("CORRIDOR_MI", "15"))
CORRIDOR_FALLBACK_MI = float(os.environ.get("CORRIDOR_FALLBACK_MI", "60"))
