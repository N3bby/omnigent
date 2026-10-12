"""Exercise the real Kubernetes exec transport against a local channel-protocol server.

Run with Kubernetes 36 and websockets installed; no cluster access is needed.
"""
import json
from pathlib import Path
import sys
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "images/server"))
import omnigent_subscription_login as login
try:
    from kubernetes import client
    from websockets.sync.server import serve
except ImportError:
    serve = None


@unittest.skipIf(serve is None, "needs Kubernetes client and websockets")
class ExecTransportTests(unittest.TestCase):
    def test_stdout_stdin_status_and_isolated_rest_client(self):
        errors = []
        def handler(ws):
            try:
                query = parse_qs(urlsplit(ws.request.path).query)
                if query.get("stdin") in (["True"], ["true"]):
                    frame = ws.recv(timeout=5)
                    self.assertIn(frame, (b'\x00audit-code#state\n', '\x00audit-code#state\n'))
                ws.send(b'\x01exec-output\n')
                ws.send(b'\x03' + json.dumps({"status":"Success"}).encode())
            except Exception as exc:
                errors.append(exc)
        with serve(handler, "127.0.0.1", 0, subprotocols=["v4.channel.k8s.io"], close_timeout=0.1) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            config = client.Configuration()
            config.host = f"http://127.0.0.1:{server.socket.getsockname()[1]}"
            api = client.ApiClient(config)
            core = client.CoreV1Api(api)
            original_request = core.api_client.request
            try:
                for code in (None, "audit-code#state"):
                    self.assertEqual(login._execute(core, "subscription-login-" + "a"*32,
                        ["/opt/venv/bin/python", "-c", "print(1)"], code), "exec-output\n")
                self.assertEqual(core.api_client.request, original_request)
                self.assertEqual(errors, [])
            finally:
                api.close()
                server.shutdown()

    def test_failed_exit_never_returns_partial_credentials(self):
        def handler(ws):
            ws.send(b'\x01partial-private-output')
            ws.send(b'\x03' + json.dumps({"status":"Failure", "details":{"causes":[{"reason":"ExitCode", "message":"1"}]}}).encode())
        with serve(handler, "127.0.0.1", 0, subprotocols=["v4.channel.k8s.io"], close_timeout=0.1) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            config = client.Configuration()
            config.host = f"http://127.0.0.1:{server.socket.getsockname()[1]}"
            api = client.ApiClient(config)
            try:
                with self.assertRaises(ValueError) as error:
                    login._execute(client.CoreV1Api(api), "subscription-login-" + "a"*32,
                        ["/opt/venv/bin/python", "-c", "print(1)"])
                self.assertNotIn("partial-private-output", str(error.exception))
            finally:
                api.close()
                server.shutdown()
