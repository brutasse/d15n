"""Steps and a workflow shared by the rollout (SIGTERM drain) tests.

Kept in its own importable module: the E2E test spawns a real worker
subprocess that resolves `slow_flow` by its dotted name.
"""

import os
import threading
import time

from d15n import step, workflow

SLEEPER_STARTED = threading.Event()


@step
def quick():
    return "q"


@step
def sleeper():
    SLEEPER_STARTED.set()
    time.sleep(float(os.environ.get("D15N_TEST_STEP_SLEEP", "0")))
    return "s"


@step
def finish():
    return "done"


@workflow
def slow_flow(args):
    quick()
    sleeper()
    return finish()
