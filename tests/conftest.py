from pathlib import Path

import pytest

from marsd.fixtures import generate


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    return generate(tmp_path / "study")
