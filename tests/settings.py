import os

DB = os.environ.get("EVERYSTEP_TEST_DB", "postgres")

if DB == "mariadb":
    import pymysql

    pymysql.install_as_MySQLdb()
    _DEFAULTS = {"ENGINE": "django.db.backends.mysql", "USER": "root", "PORT": "3306"}
else:
    _DEFAULTS = {
        "ENGINE": "django.db.backends.postgresql",
        "USER": "postgres",
        "PORT": "5432",
    }

SECRET_KEY = "everystep-tests"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "everystep",
]

DATABASES = {
    "default": {
        **_DEFAULTS,
        "NAME": os.environ.get("EVERYSTEP_TEST_DB_NAME", "everystep"),
        "USER": os.environ.get("EVERYSTEP_TEST_DB_USER", _DEFAULTS["USER"]),
        "PASSWORD": os.environ.get("EVERYSTEP_TEST_DB_PASSWORD", "postgres"),
        "HOST": os.environ.get("EVERYSTEP_TEST_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("EVERYSTEP_TEST_DB_PORT", _DEFAULTS["PORT"]),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
ROOT_URLCONF = "tests.urls"
