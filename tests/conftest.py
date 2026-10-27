from pathlib import Path

import pytest

from fprime_cfs_cosmos_generator.dictionary import FprimeDictionary

DICTIONARY = Path(__file__).parent / "data" / "FPrimeDeploymentTopologyDictionary.json"


@pytest.fixture
def dictionary():
    return FprimeDictionary(DICTIONARY)
