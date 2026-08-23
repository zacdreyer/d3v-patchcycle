from __future__ import annotations

import pytest

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir():
    return FIXTURES
