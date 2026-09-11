import pytest

from tests import dbcontainer


def pytest_sessionfinish(session, exitstatus):
    dbcontainer.stop()


@pytest.fixture(autouse=True)
def _reset_fault():
    from everystep import runner

    runner.fault = None
    yield
    runner.fault = None
