"""Loopback-only, same-origin browser API with launch-scoped bearer sessions."""

from __future__ import annotations

import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Query
from fastapi import Request as HTTPRequest
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from starlette.exceptions import HTTPException

from marsd.contracts import Contract, load_contract
from marsd.domain import MarsdError, Request, StrictModel, dumps
from marsd.http_limits import BoundedBody
from marsd.lineage import Graph
from marsd.reports import render_html
from marsd.runs import RunStore, artifact_version
from marsd.service import Workbench


class LosslessJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return dumps(content).encode("utf-8")


class Login(StrictModel):
    code: str = Field(max_length=256)


class Register(StrictModel):
    path: str = Field(max_length=8192)
    strong: bool = False


class Export(StrictModel):
    format: Literal["json", "html"] = "html"
    name: str | None = Field(default=None, max_length=256)
    overwrite: bool = False


def create_app(root: str | Path, *, launch_code: str, port: int) -> FastAPI:
    origin = f"http://127.0.0.1:{port}"
    session = secrets.token_urlsafe(32)
    expires = time.monotonic() + 300
    attempts = 0
    consumed = False
    login_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with Workbench(root) as workbench:
            app.state.workbench = workbench
            yield

    app = FastAPI(
        title="Marsd local API",
        default_response_class=LosslessJSONResponse,
        version="1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Added before the boundary so Host/Origin/session checks run before body buffering.
    app.add_middleware(BoundedBody)

    @app.middleware("http")
    async def boundary(request: HTTPRequest, call_next: Any) -> Response:
        if request.headers.get("host") != f"127.0.0.1:{port}":
            return JSONResponse(
                {
                    "error": MarsdError(
                        "INVALID_HOST", "Use the exact loopback URL printed by Marsd."
                    ).payload()
                },
                status_code=403,
            )
        supplied_origin = request.headers.get("origin")
        if (
            supplied_origin
            and supplied_origin != origin
            or request.headers.get("sec-fetch-site") == "cross-site"
        ):
            return JSONResponse(
                {
                    "error": MarsdError(
                        "INVALID_ORIGIN", "Cross-origin access is forbidden."
                    ).payload()
                },
                status_code=403,
            )
        if (
            request.headers.get("content-length", "0").isdigit()
            and int(request.headers.get("content-length", "0")) > 1024 * 1024
        ):
            return JSONResponse(
                {"error": MarsdError("BUDGET_EXCEEDED", "Request exceeds 1 MiB.").payload()},
                status_code=413,
            )
        if request.url.path.startswith("/api/") and request.url.path != "/api/session":
            if not secrets.compare_digest(
                request.headers.get("authorization", "").encode(), f"Bearer {session}".encode()
            ):
                return JSONResponse(
                    {
                        "error": MarsdError(
                            "UNAUTHORIZED",
                            "Unlock this session using the launch code from your terminal.",
                        ).payload()
                    },
                    status_code=401,
                )
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            }
        )
        return response

    @app.exception_handler(MarsdError)
    async def domain_error(request: HTTPRequest, error: MarsdError) -> JSONResponse:
        return JSONResponse(
            {"error": error.payload()}, status_code=404 if error.code == "NOT_FOUND" else 400
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: HTTPRequest, error: HTTPException) -> JSONResponse:
        return JSONResponse(
            {
                "error": MarsdError(
                    "NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR",
                    "Endpoint or resource is unavailable.",
                ).payload()
            },
            status_code=error.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: HTTPRequest, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "Request does not match the API schema.",
                    "details": [
                        {"field": str(e["loc"]), "reason": e["msg"]} for e in error.errors()
                    ],
                    "suggested_action": "Review the fields and their limits.",
                    "coverage": None,
                }
            },
            status_code=422,
        )

    @app.post("/api/session")
    def login(body: Login) -> dict[str, Any]:
        nonlocal attempts, consumed
        with login_lock:
            attempts += 1
            if (
                consumed
                or time.monotonic() > expires
                or attempts > 10
                or not secrets.compare_digest(body.code.encode(), launch_code.encode())
            ):
                raise MarsdError(
                    "INVALID_LAUNCH_CODE",
                    "Launch code is invalid, expired or already used.",
                    action="Restart marsd open for a fresh code.",
                    exit_code=2,
                )
            consumed = True
            return {"access_token": session}

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok"}

    @app.get("/api/project")
    def project() -> dict[str, Any]:
        wb = app.state.workbench
        return {
            **wb.registry.project,
            "path": str(wb.registry.root),
            "outputs": str(wb.registry.home / "reports"),
        }

    @app.get("/api/artifacts")
    def artifacts() -> list[dict[str, Any]]:
        return app.state.workbench.registry.artifacts()

    @app.post("/api/artifacts", status_code=201)
    def register(body: Register) -> dict[str, Any]:
        return app.state.workbench.registry.register(body.path, strong=body.strong)

    @app.post("/api/jobs", status_code=202)
    def submit(body: Request) -> dict[str, str]:
        return {"id": app.state.workbench.submit(body)}

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return app.state.workbench.registry.recent_jobs()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return app.state.workbench.jobs.get(job_id)

    @app.delete("/api/jobs/{job_id}")
    def cancel(job_id: str) -> dict[str, Any]:
        return app.state.workbench.jobs.cancel(job_id)

    @app.get("/api/reports")
    def reports() -> list[dict[str, Any]]:
        return app.state.workbench.registry.reports()

    @app.get("/api/versions/{version_id}")
    def version_detail(version_id: str) -> dict[str, Any]:
        return artifact_version(app.state.workbench.registry, version_id)

    @app.get("/api/runs")
    def runs(
        version_id: str | None = None,
        offset: int = Query(0, ge=0, le=100_000),
        limit: int = Query(100, ge=1, le=100),
    ) -> list[dict[str, Any]]:
        return RunStore(app.state.workbench.registry).list(
            version_id=version_id, offset=offset, limit=limit
        )

    @app.get("/api/runs/compare")
    def compare_runs(a: str, b: str) -> dict[str, Any]:
        return RunStore(app.state.workbench.registry).compare(a, b)

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        return RunStore(app.state.workbench.registry).get(run_id)

    @app.get("/api/runs/{run_id}/readiness")
    def run_readiness(run_id: str) -> dict[str, Any]:
        return RunStore(app.state.workbench.registry).readiness(run_id)

    @app.get("/api/lineage/{entity_id}")
    def lineage(
        entity_id: str,
        direction: Literal["upstream", "downstream"] = "downstream",
        depth: int = Query(1, ge=0, le=20),
        max_nodes: int = Query(200, ge=1, le=200),
        max_edges: int = Query(500, ge=1, le=500),
        include_inferred: bool = False,
    ) -> dict[str, Any]:
        return Graph(app.state.workbench.registry).query(
            entity_id,
            direction=direction,
            depth=depth,
            max_nodes=max_nodes,
            max_edges=max_edges,
            include_inferred=include_inferred,
        )

    @app.get("/api/reports/{report_id}")
    def report(report_id: str) -> dict[str, Any]:
        return app.state.workbench.registry.report(report_id)

    @app.post("/api/reports/{report_id}/export")
    def export(report_id: str, body: Export) -> dict[str, str]:
        path = app.state.workbench.export(
            report_id, format=body.format, name=body.name, overwrite=body.overwrite
        )
        return {"path": str(path), "name": path.name}

    @app.get("/api/reports/{report_id}/download")
    def download(report_id: str, format: Literal["json", "html"] = "html") -> Response:
        result = app.state.workbench.registry.report(report_id)
        content = render_html(result) if format == "html" else dumps(result)
        return Response(
            content,
            media_type="text/html" if format == "html" else "application/json",
            headers={"Content-Disposition": f'attachment; filename="marsd-report.{format}"'},
        )

    @app.get("/api/contracts")
    def contracts() -> list[dict[str, Any]]:
        registry = app.state.workbench.registry
        result: list[dict[str, Any]] = []
        directory = registry.root / "contracts"
        if directory.is_dir():
            for p in sorted(directory.glob("*.yaml"))[:100]:
                try:
                    contract = load_contract(registry.source(p))
                    result.append(
                        {
                            "path": str(p),
                            "name": contract.name,
                            "revision": contract.revision,
                            "rules": [r.model_dump(exclude_none=True) for r in contract.rules],
                        }
                    )
                except MarsdError:
                    result.append({"path": str(p), "name": p.name, "invalid": True})
        return result

    @app.get("/api/schema/contract")
    def contract_schema() -> dict[str, Any]:
        return Contract.model_json_schema()

    @app.get("/api/schema/openapi")
    def api_schema() -> dict[str, Any]:
        return app.openapi()

    static = Path(__file__).parent / "static"
    if (static / "index.html").is_file():
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    else:

        @app.get("/")
        def missing_ui() -> HTMLResponse:
            return HTMLResponse(
                "<h1>Marsd UI assets are missing</h1><p>Build with npm --prefix web run build, or install a wheel containing the UI.</p>",
                status_code=503,
            )

    return app
