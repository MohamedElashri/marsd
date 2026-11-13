import json

import pytest
from fastapi.testclient import TestClient

from marsd.api import create_app
from marsd.domain import MarsdError
from marsd.registry import Registry
from marsd.reports import render_html

pytestmark = pytest.mark.security


@pytest.fixture
def client(demo):
    app = create_app(demo, launch_code="single-use-test-code", port=8765)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        yield client


def authorize(client):
    response = client.post("/api/session", json={"code": "single-use-test-code"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_reads_writes_and_job_progress_require_session(client, demo):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/project").status_code == 401
    assert client.get("/api/jobs/unknown").status_code == 401
    assert client.get("/api/reports").status_code == 401
    assert (
        client.post(
            "/api/jobs", json={"operation": "inspect", "path": str(demo / "data/run_042.h5")}
        ).status_code
        == 401
    )
    headers = authorize(client)
    assert client.get("/api/project", headers=headers).status_code == 200
    assert client.get("/api/artifacts", headers=headers).json()[0]["name"]
    assert client.post("/api/session", json={"code": "single-use-test-code"}).status_code == 400


def test_origin_host_and_rebinding_rejected_even_with_token(client):
    headers = authorize(client)
    assert (
        client.get(
            "/api/project", headers={**headers, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/project", headers={**headers, "Host": "evil.example:8765"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/session", json={"code": "x"}, headers={"Sec-Fetch-Site": "cross-site"}
        ).status_code
        == 403
    )
    response = client.get("/api/project", headers={**headers, "Origin": "http://127.0.0.1:8765"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_unknown_fields_and_invalid_selections_rejected(client, demo):
    headers = authorize(client)
    response = client.post(
        "/api/jobs",
        headers=headers,
        json={
            "operation": "inspect",
            "path": str(demo / "data/run_042.h5"),
            "execute": "arbitrary code",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert (
        client.post(
            "/api/artifacts", headers=headers, json={"path": str(demo.parent / "outside.csv")}
        ).status_code
        == 400
    )


def test_path_containment_not_prefix_and_symlink_escape(demo):
    outside = demo.parent / "study-sibling"
    outside.mkdir()
    source = outside / "secret.csv"
    source.write_text("secret\nvalue\n")
    registry = Registry(demo)
    try:
        with pytest.raises(MarsdError, match="outside"):
            registry.source(source)
        link = demo / "data/escape.csv"
        try:
            link.symlink_to(source)
        except OSError:
            pytest.skip("OS does not allow symlink creation in this test context")
        with pytest.raises(MarsdError, match="outside"):
            registry.source(link)
        for remote in [
            "https://host/data.csv",
            "//server/share/data.csv",
            "\\\\server\\share\\data.csv",
        ]:
            with pytest.raises(MarsdError):
                registry.source(remote)
    finally:
        registry.close()


def test_reports_escape_all_source_strings():
    malicious = "<script>globalThis.owned=1</script><img src=x onerror=alert(1)>"
    output = render_html(
        {"kind": malicious, "inputs": [{"name": malicious}], "findings": [malicious]}
    )
    assert "<script>" not in output
    assert "<img src=" not in output
    assert "&lt;script&gt;" in output
    assert "default-src 'none'" in output
    assert "https://" not in output


def test_backup_does_not_include_source_contents(demo):
    registry = Registry(demo)
    try:
        backup = registry.backup("private-metadata.sqlite")
        assert b"<script>never execute</script>" not in backup.read_bytes()
        with pytest.raises(MarsdError):
            registry.backup("../../outside.sqlite")
    finally:
        registry.close()


def test_non_ascii_launch_code_returns_structured_error(client):
    response = client.post("/api/session", json={"code": "ليس الرمز"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_LAUNCH_CODE"


def test_backup_rejects_report_directory_symlink_escape(demo):
    registry = Registry(demo)
    try:
        directory = demo / ".marsd/reports"
        directory.rmdir()
        target = demo.parent / "outside-reports"
        target.mkdir()
        try:
            directory.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation is unavailable in this environment")
        with pytest.raises(MarsdError, match="escapes"):
            registry.backup("snapshot.sqlite")
        assert not list(target.iterdir())
    finally:
        registry.close()


def test_chunked_request_body_limit_is_enforced_before_json_parsing(client):
    def chunks():
        yield b'{"code":"'
        for _ in range(17):
            yield b"x" * (64 * 1024)
        yield b'"}'

    response = client.post(
        "/api/session", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BUDGET_EXCEEDED"
    assert response.headers["cache-control"] == "no-store"


def test_registry_symlink_cannot_modify_external_database(demo, tmp_path):
    import sqlite3

    external = tmp_path / "external.sqlite"
    with sqlite3.connect(external) as connection:
        connection.execute("CREATE TABLE scientific_data(value INTEGER)")
    database = demo / ".marsd/registry.sqlite"
    database.unlink()
    try:
        database.symlink_to(external)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    before = external.read_bytes()
    with pytest.raises(MarsdError, match="regular files"):
        Registry(demo)
    assert external.read_bytes() == before


def test_malformed_manifest_is_a_structured_error_without_opening_database(demo):
    path = demo / ".marsd/project.json"
    config = json.loads(path.read_text())
    config["source_roots"] = "not a list"
    path.write_text(json.dumps(config))
    with pytest.raises(MarsdError, match="manifest"):
        Registry(demo)
