"""Private native-owner protocol; shared HTTP security remains unchanged."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

import psutil
import uvicorn

from marsd.api import create_app
from marsd.domain import MarsdError, dumps
from marsd.registry import Registry, create_project


async def serve(project: Path, owner_pid: int, *, create: bool, roots: list[str]) -> None:
    if create:
        create_project(project, source_roots=roots or None)
    registry = Registry(project)
    registry.close()
    owner = psutil.Process(owner_pid)
    owner_created = owner.create_time()
    code = secrets.token_urlsafe(32)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(project, launch_code=code, port=port),
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="warning",
        )
    )

    def watch_owner() -> None:
        while not server.should_exit:
            time.sleep(0.2)
            try:
                if (
                    owner.is_running()
                    and owner.status() != psutil.STATUS_ZOMBIE
                    and owner.create_time() == owner_created
                ):
                    continue
            except psutil.Error:
                pass
            server.should_exit = True

    control_fd = sys.stdin.fileno()
    os.set_blocking(control_fd, False)

    def control() -> None:
        # Never hold buffered/CRT stdin locks while workers spawn or the owner exits.
        line = bytearray()
        while not server.should_exit:
            try:
                byte = os.read(control_fd, 1)
            except BlockingIOError:
                time.sleep(0.05)
                continue
            except OSError:
                byte = b""
            line.extend(byte)
            if not byte or line == b"shutdown\n" or len(line) > 64:
                server.should_exit = True
                return
            if byte == b"\n":
                line.clear()

    threading.Thread(target=watch_owner, daemon=True).start()
    threading.Thread(target=control, daemon=True).start()
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        deadline = time.monotonic() + 30
        while not server.started:
            if task.done():
                await task
                raise MarsdError(
                    "BACKEND_START_FAILED", "The project backend did not finish startup."
                )
            if time.monotonic() > deadline:
                server.should_exit = True
                raise MarsdError(
                    "BACKEND_START_TIMEOUT", "The project backend exceeded its startup budget."
                )
            await asyncio.sleep(0.02)
        print(dumps({"protocol": 1, "pid": os.getpid(), "port": port, "code": code}), flush=True)
        sys.stdout = sys.stderr
        await task
    finally:
        server.should_exit = True
        if not task.done():
            await task
        listener.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--owner-pid", type=int, required=True)
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--source-root", action="append", default=[])
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.project, args.owner_pid, create=args.create, roots=args.source_root))
    except (MarsdError, OSError, psutil.Error) as error:
        value = (
            error.payload()
            if isinstance(error, MarsdError)
            else {
                "code": "BACKEND_START_FAILED",
                "message": "Native backend startup failed.",
                "details": {"exception_type": type(error).__name__},
            }
        )
        print(dumps({"error": value}), flush=True)
        raise SystemExit(3) from error
