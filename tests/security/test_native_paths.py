"""Run native path/alias semantics on each OS; Windows junctions need no symlink privilege."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from marsd.domain import MarsdError, Request
from marsd.registry import Registry, create_project
from marsd.service import Workbench

pytestmark = pytest.mark.security


def directory_alias(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            check=True,
            capture_output=True,
        )
        assert link.is_junction()
    else:
        link.symlink_to(target, target_is_directory=True)


def test_directory_alias_cannot_escape_source_root(demo, tmp_path):
    outside = tmp_path / "outside-source"
    outside.mkdir()
    source = outside / "private.csv"
    source.write_text("id\n0001\n")
    before = source.read_bytes()
    link = demo / "data" / "directory-alias"
    directory_alias(link, outside)
    registry = Registry(demo)
    try:
        with pytest.raises(MarsdError) as error:
            registry.register(link / source.name)
        assert error.value.code == "PATH_NOT_ALLOWED"
        assert source.read_bytes() == before
    finally:
        registry.close()
        link.rmdir() if os.name == "nt" else link.unlink()


def test_metadata_directory_alias_cannot_modify_other_project(demo, tmp_path):
    wrapper = tmp_path / "wrapper"
    wrapper.mkdir()
    home = demo / ".marsd"
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in home.iterdir() if p.is_file()
    }
    link = wrapper / ".marsd"
    directory_alias(link, home)
    try:
        for operation in (Registry, create_project):
            with pytest.raises(MarsdError) as error:
                operation(wrapper)
            assert error.value.code == "PATH_NOT_ALLOWED"
        after = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in home.iterdir()
            if p.is_file()
        }
        assert before == after
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("filename", ["CaseProbe.csv", "ΔΕΔΟΜΕΝΑ.csv"])
def test_case_aliases_follow_actual_filesystem_identity(demo, filename):
    upper = demo / "data" / filename
    lower = upper.with_name(filename.lower())
    upper.write_text("id\n0001\n")
    insensitive = lower.exists()
    if not insensitive:
        lower.write_text("id\n0002\n")
    registry = Registry(demo)
    try:
        a = registry.register(upper)
        b = registry.register(lower)
        assert (a["id"] == b["id"]) == insensitive
    finally:
        registry.close()


def test_long_unicode_source_path_is_read_without_modification(demo):
    directory = demo / "data"
    for i in range(5):
        directory /= f"study_{i}_" + "x" * 48
    directory.mkdir(parents=True)
    source = directory / "مرصد measurement.csv"
    source.write_text("id,value\n0001,42\n", encoding="utf-8")
    assert len(str(source)) > 260
    before = source.read_bytes()
    with Workbench(demo) as wb:
        result = wb.execute(Request(operation="preview", path=str(source)))
        assert result["rows"][0] == {"id": "0001", "value": "42"}
    assert source.read_bytes() == before
