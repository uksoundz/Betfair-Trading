from datetime import date

import pytest

from tradescout.data import OpenFootballProvider
from tradescout.model import Forecaster

AS_OF = date(2025, 11, 8)


@pytest.fixture(scope="session")
def provider():
    return OpenFootballProvider()


@pytest.fixture(scope="session")
def forecaster(provider):
    return Forecaster.fit(provider.results(before=AS_OF), AS_OF)


@pytest.fixture(scope="session")
def fixtures(provider):
    return provider.fixtures(AS_OF)
