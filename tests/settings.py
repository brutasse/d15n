import os

SECRET_KEY = "d15n-tests"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "d15n",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "d15n",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "127.0.0.1",
        "PORT": os.environ.get("D15N_TEST_PG_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
