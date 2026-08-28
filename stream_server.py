"""Local HTTP and WebSocket relay used by the OBS browser source."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.server import serve


class _StaticHandler(SimpleHTTPRequestHandler):
    server_version = "AutoLive2DStream/1.0"

    def __init__(self, *args, directory: str, runtime_dir: Path, health_provider: Callable[[], dict], **kwargs):
        self.runtime_dir = runtime_dir
        self.health_provider = health_provider
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        request_path = self.path.split("?", 1)[0]
        if request_path == "/runtime/health":
            payload = json.dumps(self.health_provider(), ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if request_path == "/runtime/current.psd":
            model_path = self.runtime_dir / "current.psd"
            if not model_path.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "No model has been loaded yet")
                return
            payload = model_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/vnd.adobe.photoshop")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def log_message(self, format, *args):
        if os.environ.get("AUTO_LIVE2D_HTTP_LOG") == "1":
            super().log_message(format, *args)


class _RelayHTTPServer(ThreadingHTTPServer):
    # Do not let a second server bind the same port on Windows.  A stale
    # ``python -m http.server`` must fail loudly instead of winning requests
    # while the WebSocket relay runs on a different process.
    allow_reuse_address = False
    daemon_threads = True


class StreamRelay:
    START_TIMEOUT = 5.0

    def __init__(self, project_root: Path, http_port: int = 18765, ws_port: int = 18766):
        self.project_root = project_root.resolve()
        self.runtime_dir = self.project_root / ".runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.http_port = http_port
        self.ws_port = ws_port
        self.http_server: ThreadingHTTPServer | None = None
        self.http_thread: threading.Thread | None = None
        self.ws_thread: threading.Thread | None = None
        self.ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_stop_event: asyncio.Event | None = None
        self._ws_ready = threading.Event()
        self._ws_stop_requested = threading.Event()
        self._ws_error: BaseException | None = None
        self._clients: set = set()
        self._roles: dict = {}
        self._pending_model: dict = {}
        self._model_version = 0
        self._model_name = ""

    @property
    def control_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}/index.html"

    @property
    def overlay_url(self) -> str:
        return f"http://127.0.0.1:{self.http_port}/index.html?overlay=1&wsPort={self.ws_port}"

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def websocket_ready(self) -> bool:
        return bool(
            self._ws_ready.is_set()
            and self._ws_error is None
            and self.ws_thread is not None
            and self.ws_thread.is_alive()
        )

    def health(self) -> dict:
        return {
            "http": bool(self.http_thread and self.http_thread.is_alive()),
            "websocket": self.websocket_ready,
            "websocketPort": self.ws_port,
            "modelReady": (self.runtime_dir / "current.psd").exists(),
            "modelVersion": self._model_version,
            "modelName": self._model_name,
            "error": str(self._ws_error) if self._ws_error else None,
        }

    def start(self):
        if self.http_server is not None or (self.ws_thread and self.ws_thread.is_alive()):
            raise RuntimeError("Stream relay is already running")

        self._ws_ready.clear()
        self._ws_stop_requested.clear()
        self._ws_error = None
        handler = lambda *args, **kwargs: _StaticHandler(
            *args,
            directory=str(self.project_root),
            runtime_dir=self.runtime_dir,
            health_provider=self.health,
            **kwargs,
        )

        try:
            self.http_server = _RelayHTTPServer(("127.0.0.1", self.http_port), handler)
        except OSError as error:
            raise RuntimeError(
                f"Cannot start the HTTP relay on 127.0.0.1:{self.http_port}: {error}. "
                "Close any old static server and start the app with run.bat."
            ) from error

        self.http_thread = threading.Thread(target=self.http_server.serve_forever, name="auto-live2d-http", daemon=True)
        self.http_thread.start()

        self.ws_thread = threading.Thread(target=self._run_ws, name="auto-live2d-ws", daemon=True)
        try:
            self.ws_thread.start()
            if not self._ws_ready.wait(timeout=self.START_TIMEOUT):
                raise RuntimeError(
                    f"WebSocket relay did not become ready on 127.0.0.1:{self.ws_port} "
                    f"within {self.START_TIMEOUT:.0f} seconds"
                )
            if self._ws_error is not None:
                raise RuntimeError(
                    f"Cannot start the WebSocket relay on 127.0.0.1:{self.ws_port}: {self._ws_error}"
                ) from self._ws_error
        except BaseException:
            self.stop()
            raise

    def stop(self):
        self._ws_stop_requested.set()

        loop = self.ws_loop
        stop_event = self._ws_stop_event
        if loop and stop_event and not loop.is_closed():
            loop.call_soon_threadsafe(stop_event.set)

        if self.http_server:
            server = self.http_server
            self.http_server = None
            server.shutdown()
            server.server_close()
        if self.http_thread and self.http_thread is not threading.current_thread():
            self.http_thread.join(timeout=self.START_TIMEOUT)
        if self.ws_thread and self.ws_thread is not threading.current_thread():
            self.ws_thread.join(timeout=self.START_TIMEOUT)

    def _run_ws(self):
        loop = asyncio.new_event_loop()
        self.ws_loop = loop
        asyncio.set_event_loop(loop)

        async def runner():
            stop_event = asyncio.Event()
            self._ws_stop_event = stop_event
            if self._ws_stop_requested.is_set():
                stop_event.set()
            try:
                async with serve(self._handle_ws, "127.0.0.1", self.ws_port, max_size=64 * 1024 * 1024):
                    self._ws_ready.set()
                    await stop_event.wait()
            except BaseException as error:
                self._ws_error = error
                self._ws_ready.set()
                raise
            finally:
                self._ws_stop_event = None

        try:
            loop.run_until_complete(runner())
        except BaseException as error:
            if self._ws_error is None:
                self._ws_error = error
                self._ws_ready.set()
        finally:
            self.ws_loop = None
            loop.close()

    async def _send_status(self, websocket):
        await websocket.send(json.dumps({
            "type": "server-status",
            "modelReady": (self.runtime_dir / "current.psd").exists(),
            "modelVersion": self._model_version,
            "modelName": self._model_name,
        }))

    async def _broadcast(self, payload: str, role: str | None = None, exclude=None):
        stale = []
        for client in tuple(self._clients):
            if client is exclude or (role and self._roles.get(client) != role):
                continue
            try:
                await client.send(payload)
            except Exception:
                stale.append(client)
        for client in stale:
            self._clients.discard(client)
            self._roles.pop(client, None)
            self._pending_model.pop(client, None)

    async def _handle_ws(self, websocket):
        self._clients.add(websocket)
        await self._send_status(websocket)
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    metadata = self._pending_model.pop(websocket, None)
                    if not metadata:
                        continue
                    temp_path = self.runtime_dir / "current.psd.tmp"
                    final_path = self.runtime_dir / "current.psd"
                    temp_path.write_bytes(message)
                    temp_path.replace(final_path)
                    self._model_version += 1
                    self._model_name = str(metadata.get("name") or "model.psd")
                    notice = json.dumps({
                        "type": "model-ready",
                        "version": self._model_version,
                        "name": self._model_name,
                    })
                    await self._broadcast(notice, role="overlay")
                    await websocket.send(notice)
                    continue

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue
                kind = data.get("type")
                if kind == "hello":
                    self._roles[websocket] = data.get("role", "control")
                    await self._send_status(websocket)
                elif kind == "model-meta" and self._roles.get(websocket) == "control":
                    self._pending_model[websocket] = data
                elif kind in {"state", "config"} and self._roles.get(websocket) == "control":
                    await self._broadcast(message, role="overlay", exclude=websocket)
        finally:
            self._clients.discard(websocket)
            self._roles.pop(websocket, None)
            self._pending_model.pop(websocket, None)
