import pytest

from tests import pgcontainer


def pytest_sessionfinish(session, exitstatus):
    pgcontainer.stop()


@pytest.fixture(autouse=True)
def _reset_fault():
    from d15n import runner

    runner.fault = None
    yield
    runner.fault = None
