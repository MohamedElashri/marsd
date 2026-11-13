"""Release checks select the configured product version."""

import runpy
from pathlib import Path

import pytest


def test_wheel_checks_refuse_stale_higher_versions(tmp_path):
    select = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_wheel.py"))[
        "current_wheel"
    ]
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "marsd"\nversion = "0.1.0"\n')
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "marsd-0.9.0-py3-none-any.whl").touch()
    with pytest.raises(FileNotFoundError, match="marsd-0.1.0"):
        select(tmp_path)
    current = dist / "marsd-0.1.0-py3-none-any.whl"
    current.touch()
    assert select(tmp_path) == current
