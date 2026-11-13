"""Verify the wheel carries runtime UI, migration, schema and licensing resources."""

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]


def current_wheel(project=root):
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    wheel = project / "dist" / f"{metadata['name']}-{metadata['version']}-py3-none-any.whl"
    if not wheel.is_file():
        raise FileNotFoundError(f"Build the current version first: {wheel.name}")
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
        html = archive.read("marsd/static/index.html").decode("utf-8")
        for reference in re.findall(r'(?:src|href)="([^"]+)"', html):
            link = urlsplit(reference)
            assert not link.scheme and not link.netloc, (
                f"UI requires an external resource: {reference}"
            )
            path = unquote(link.path).lstrip("/")
            assert ".." not in path.split("/"), f"UI resource escapes its bundle: {reference}"
            assert f"marsd/static/{path}" in names, f"Missing referenced UI asset: {reference}"
    print(f"Wheel resources verified: {wheel.name}")


if __name__ == "__main__":
    check(current_wheel())
