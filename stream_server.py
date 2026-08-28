"""Local HTTP and WebSocket relay used by the OBS browser source."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websockets.asyncio.server import serve


class _StaticHandler(SimpleHTTPRequestHandler):
    server_version = "AutoLive2DStream/1.0"

    def __init__(self, *args, directory: str, runtime_dir: Path, **kwargs):
        self.runtime_dir = runtime_dir
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/runtime/current.psd":
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


class StreamRelay:
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

    def start(self):
        handler = lambda *args, **kwargs: _StaticHandler(
            *args,
            directory=str(self.project_root),
            runtime_dir=self.runtime_dir,
            **kwargs,
        )
        self.http_server = ThreadingHTTPServer(("127.0.0.1", self.http_port), handler)
        self.http_thread = threading.Thread(target=self.http_server.serve_forever, name="auto-live2d-http", daemon=True)
        self.http_thread.start()

        self.ws_thread = threading.Thread(target=self._run_ws, name="auto-live2d-ws", daemon=True)
        self.ws_thread.start()

    def stop(self):
        if self.http_server:
            self.http_server.shutdown()
            self.http_server.server_close()
        if self.ws_loop:
            self.ws_loop.call_soon_threadsafe(self.ws_loop.stop)

    def _run_ws(self):
        loop = asyncio.new_event_loop()
        self.ws_loop = loop
        asyncio.set_event_loop(loop)

        async def runner():
            async with serve(self._handle_ws, "127.0.0.1", self.ws_port, max_size=64 * 1024 * 1024):
                await asyncio.Future()

        try:
            loop.run_until_complete(runner())
        except RuntimeError as error:
            if "Event loop stopped" not in str(error):
                raise
        finally:
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
