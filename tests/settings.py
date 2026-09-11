import os

SECRET_KEY = "everystep-tests"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "everystep",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "everystep",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("EVERYSTEP_TEST_PG_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ROOT_URLCONF = "tests.urls"
