from pathlib import Path

import pytest
from fprime_cosmos.dictionary import FprimeDictionary

DICTIONARY = Path(__file__).parent / "data" / "FPrimeDeploymentTopologyDictionary.json"


@pytest.fixture
def dictionary():
    return FprimeDictionary(DICTIONARY)
