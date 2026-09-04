"""Subprocess entrypoint for the rollout E2E test: run a real d15n worker.

Run as `python -m tests.rollout_worker` from the repo root. Reads:
  D15N_E2E_DB      test database name to connect to
  D15N_E2E_NAME    stable runner name
  D15N_E2E_DRAIN   drain deadline in seconds (default 2)
"""

import os

os.environ["DJANGO_SETTINGS_MODULE"] = "tests.settings"

import django

django.setup()

db_name = os.environ.get("D15N_E2E_DB")
if db_name:
    from django.conf import settings

    settings.DATABASES["default"]["NAME"] = db_name

from django.core.management import call_command

call_command(
    "d15n_worker",
    name=os.environ["D15N_E2E_NAME"],
    poll=0.1,
    drain=float(os.environ.get("D15N_E2E_DRAIN", "2")),
)
