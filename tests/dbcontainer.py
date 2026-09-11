"""Start a throwaway database container for the test suite.

Loaded as a pytest plugin (-p tests.dbcontainer) so that the environment is
ready before pytest-django imports the settings module. The backend is
selected with EVERYSTEP_TEST_DB (postgres by default, or mariadb).
"""

import os
import socket
import subprocess
import time

BACKENDS = {
    "postgres": {
        "image_env": "EVERYSTEP_TEST_PG_IMAGE",
        "image": "postgres:16",
        "port_env": "EVERYSTEP_TEST_PG_PORT",
        "container": "everystep-test-pg",
        "env": ["POSTGRES_USER=postgres", "POSTGRES_PASSWORD=postgres"],
        "port": 5432,
        "ready": ["pg_isready", "-U", "postgres"],
    },
    "mariadb": {
        "image_env": "EVERYSTEP_TEST_MARIADB_IMAGE",
        "image": "mariadb:11",
        "port_env": "EVERYSTEP_TEST_MARIADB_PORT",
        "container": "everystep-test-mariadb",
        "env": ["MARIADB_ROOT_PASSWORD=postgres"],
        "port": 3306,
        "ready": [
            "mariadb",
            "--user",
            "root",
            "--password=postgres",
            "--protocol=TCP",
            "--host",
            "127.0.0.1",
            "--execute",
            "SELECT 1",
        ],
    },
}

DB = os.environ.get("EVERYSTEP_TEST_DB", "postgres")
CONFIG = BACKENDS[DB]
CONTAINER = CONFIG["container"]
IMAGE = os.environ.get(CONFIG["image_env"], CONFIG["image"])

own_container = not bool(os.environ.get(CONFIG["port_env"]))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start_container():
    port = _free_port()
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    args = ["docker", "run", "-d", "--name", CONTAINER]
    for env in CONFIG["env"]:
        args += ["-e", env]
    args += ["-p", f"127.0.0.1:{port}:{CONFIG['port']}", IMAGE]
    run = subprocess.run(args, capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError(f"cannot start {DB} container: {run.stderr}")
    deadline = time.time() + 90
    ready = ["docker", "exec", CONTAINER, *CONFIG["ready"]]
    while time.time() < deadline:
        if subprocess.run(ready, capture_output=True).returncode == 0:
            return port
        time.sleep(0.25)
    raise RuntimeError(f"{DB} container did not become ready in 90s")


if own_container:
    os.environ["EVERYSTEP_TEST_DB_PORT"] = str(_start_container())
else:
    os.environ["EVERYSTEP_TEST_DB_PORT"] = os.environ[CONFIG["port_env"]]


def stop():
    if own_container:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
