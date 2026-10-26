from pathlib import Path

import pytest
from fprime_cosmos.dictionary import FprimeDictionary

DICTIONARY = (
    Path(__file__).parents[2] / "fprime-cosmos" / "tests" / "data" / "FPrimeDeploymentTopologyDictionary.json"
)


@pytest.fixture
def dictionary():
    if not DICTIONARY.is_file():
        pytest.skip("fprime-cosmos integration dictionary is unavailable")
    return FprimeDictionary(DICTIONARY)
