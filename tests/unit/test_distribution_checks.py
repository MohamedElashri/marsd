import importlib
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def checks(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("check_docs"), importlib.import_module("check_wheel")


def test_document_links_require_existing_headings(checks, tmp_path, monkeypatch):
    docs, _ = checks
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("# Project\n[Guide](docs/usage.md#using-data)\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n")
    guide = tmp_path / "docs/usage.md"
    guide.write_text("# Using data\n")
    docs.check()
    guide.write_text("# Renamed guide\n")
    with pytest.raises(SystemExit, match="missing heading"):
        docs.check()


def test_document_anchors_handle_duplicates_and_ignore_code(checks):
    docs, _ = checks
    assert docs.anchors("# Data & units\n## Read\n## Read\n```sh\n# Not a heading\n```\n") == {
        "data--units",
        "read",
        "read-1",
    }


def test_source_fingerprint_ignores_local_files_but_detects_tracked_edits(
    checks, tmp_path, monkeypatch
):
    module = importlib.import_module("source_checkpoint")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/module.py"
    source.write_text("value = 1\n")
    (tmp_path / ".gitignore").write_text("src/local-only.txt\n")
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", ".gitignore", "src/module.py"], check=True)
    before = module.checkpoint()
    (tmp_path / "src/local-only.txt").write_text("Local-only content\n")
    assert module.checkpoint() == before
    source.write_text("value = 2\n")
    assert module.checkpoint()["sha256"] != before["sha256"]


@pytest.mark.parametrize(
    "reference,error",
    [
        (None, None),
        ("/assets/missing.js", "Missing referenced UI asset"),
        ("https://example.invalid/app.js", "external resource"),
        ("../app.js", "escapes its bundle"),
    ],
)
def test_wheel_checks_the_assets_loaded_by_its_index(checks, tmp_path, reference, error):
    _, wheel = checks
    path = tmp_path / "candidate.whl"
    with ZipFile(path, "w") as archive:
        for directory in ("static", "migrations", "schemas"):
            for source in (ROOT / "src/marsd" / directory).rglob("*"):
                if not source.is_file():
                    continue
                name = source.relative_to(ROOT / "src").as_posix()
                if name == "marsd/static/index.html" and reference:
                    archive.writestr(name, f'<script src="{reference}"></script>')
                else:
                    archive.write(source, name)
        archive.writestr("marsd.dist-info/licenses/LICENSE", "Test license")
    if error:
        with pytest.raises(AssertionError, match=error):
            wheel.check(path)
    else:
        wheel.check(path)
