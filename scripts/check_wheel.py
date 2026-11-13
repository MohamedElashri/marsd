"""Verify the wheel carries runtime UI, migration, schema and licensing resources."""

import tomllib
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]


def current_wheel(project=root):
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    wheel = project / "dist" / f"{metadata['name']}-{metadata['version']}-py3-none-any.whl"
    if not wheel.is_file():
        raise FileNotFoundError(f"Build the current candidate first: {wheel.name}")
    return wheel


def check(wheel):
    with ZipFile(wheel) as archive:
        names = archive.namelist()
        for required in [
            "marsd/static/index.html",
            "marsd/static/third-party-licenses.txt",
            "marsd/migrations/001.sql",
            "marsd/migrations/002.sql",
            "marsd/migrations/003.sql",
            "marsd/migrations/004.sql",
            "marsd/schemas/run.json",
            "marsd/schemas/run-event.json",
            "marsd/schemas/lineage.json",
            "marsd/schemas/contract.json",
            "marsd/schemas/openapi.json",
        ]:
            assert required in names, f"Missing wheel resource: {required}"
        assert any(n.startswith("marsd/static/assets/") and n.endswith(".js") for n in names)
        assert any(n.startswith("marsd/static/assets/") and n.endswith(".css") for n in names)
        assert any(n.endswith("/licenses/LICENSE") for n in names)
    print(f"Wheel resources verified: {wheel.name}")


if __name__ == "__main__":
    check(current_wheel())
