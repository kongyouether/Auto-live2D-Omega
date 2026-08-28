from __future__ import annotations

import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

from stream_server import StreamRelay


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class StreamRelayTests(unittest.TestCase):
    def test_start_rejects_an_http_port_already_in_use(self):
        http_port = _free_port()
        ws_port = _free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            blocker.bind(("127.0.0.1", http_port))
            blocker.listen(1)
            with tempfile.TemporaryDirectory() as temp:
                relay = StreamRelay(Path(temp), http_port=http_port, ws_port=ws_port)
                with self.assertRaisesRegex(RuntimeError, "HTTP relay"):
                    relay.start()

    def test_start_rejects_a_websocket_port_already_in_use(self):
        http_port = _free_port()
        ws_port = _free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            blocker.bind(("127.0.0.1", ws_port))
            blocker.listen(1)
            with tempfile.TemporaryDirectory() as temp:
                relay = StreamRelay(Path(temp), http_port=http_port, ws_port=ws_port)
                with self.assertRaisesRegex(RuntimeError, "WebSocket relay"):
                    relay.start()
                self.assertFalse(relay.http_server)
                self.assertFalse(relay.http_thread and relay.http_thread.is_alive())

    def test_control_overlay_and_http_model_flow(self):
        http_port = _free_port()
        ws_port = _free_port()
        with tempfile.TemporaryDirectory() as temp:
            relay = StreamRelay(Path(temp), http_port=http_port, ws_port=ws_port)
            relay.start()
            try:
                self.assertTrue(relay.websocket_ready)
                health = json.loads(
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{http_port}/runtime/health", timeout=5
                    ).read()
                )
                self.assertTrue(health["http"])
                self.assertTrue(health["websocket"])

                with connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as control:
                    self.assertEqual(json.loads(control.recv(timeout=5))["type"], "server-status")
                    control.send(json.dumps({"type": "hello", "role": "control"}))
                    self.assertEqual(json.loads(control.recv(timeout=5))["type"], "server-status")

                    with connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5) as overlay:
                        self.assertEqual(json.loads(overlay.recv(timeout=5))["type"], "server-status")
                        overlay.send(json.dumps({"type": "hello", "role": "overlay"}))
                        self.assertEqual(json.loads(overlay.recv(timeout=5))["type"], "server-status")

                        control.send(json.dumps({"type": "model-meta", "name": "sample.psd", "size": 4}))
                        control.send(b"PSD!")

                        self.assertEqual(json.loads(control.recv(timeout=5))["type"], "model-ready")
                        self.assertEqual(json.loads(overlay.recv(timeout=5))["type"], "model-ready")
                        model = urllib.request.urlopen(
                            f"http://127.0.0.1:{http_port}/runtime/current.psd", timeout=5
                        ).read()
                        self.assertEqual(model, b"PSD!")

                        control.send(json.dumps({"type": "state", "params": {"angleX": 0.5}}))
                        state = json.loads(overlay.recv(timeout=5))
                        self.assertEqual(state["type"], "state")
                        self.assertEqual(state["params"]["angleX"], 0.5)
            finally:
                relay.stop()

            self.assertFalse(relay.ws_thread and relay.ws_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
