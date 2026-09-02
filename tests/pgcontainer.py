"""Start a throwaway Postgres container for the test suite.

Loaded as a pytest plugin (-p tests.pgcontainer) so that the environment is
ready before pytest-django imports the settings module.
"""

import os
import socket
import subprocess
import time

CONTAINER = "d15n-test-pg"
IMAGE = os.environ.get("D15N_TEST_PG_IMAGE", "postgres:16")

own_container = not bool(os.environ.get("D15N_TEST_PG_PORT"))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_container():
    port = _free_port()
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    run = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "-e",
            "POSTGRES_USER=postgres",
            "-e",
            "POSTGRES_PASSWORD=postgres",
            "-p",
            f"127.0.0.1:{port}:5432",
            IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        raise RuntimeError(f"cannot start postgres container: {run.stderr}")
    deadline = time.time() + 90
    while time.time() < deadline:
        ready = subprocess.run(
            ["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"],
            capture_output=True,
        )
        if ready.returncode == 0:
            return port
        time.sleep(0.25)
    raise RuntimeError("postgres container did not become ready in 90s")


if own_container:
    os.environ["D15N_TEST_PG_PORT"] = str(_start_container())


def stop():
    if own_container:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
