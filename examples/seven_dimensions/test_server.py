"""Local workbench tests; no real credentials or external model calls.

Use http.client for loopback HTTP while the model transport is disabled.
"""

import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

import client as client_module
import run as runner
import server as workbench


class StubManager:
    """Keep HTTP validation/error tests independent of worker timing."""

    def __init__(self):
        self.busy = False
        self.started = []

    def config(self):
        return {"model": "unit-test-model", "key_configured": False}

    def start(self, payload):
        if payload.get("case_id") != "normal":
            raise ValueError("未知用例")
        if self.busy:
            raise RuntimeError("已有任务正在运行")
        self.started.append(payload)
        return "11111111111141118111111111111111"

    def snapshot(self, run_id):
        if run_id != "11111111111141118111111111111111":
            raise KeyError(run_id)
        return {"run_id": run_id, "status": "running", "events": [],
                "results": [], "report_url": None, "error": None, "elapsed_ms": 1}

    def report_path(self, run_id):
        return None


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.manager = StubManager()
        self.server = workbench.create_server(port=0, manager=self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, payload, headers=None):
        request_headers = {"Content-Type": "application/json", "Origin": self.origin}
        request_headers.update(headers or {})
        return self.request("/api/runs", "POST", json.dumps(payload), request_headers)

    def test_page_assets_and_public_case_metadata_do_not_start_jobs(self):
        for path in ("/", "/app.js", "/styles.css"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(200, status)
                self.assertTrue(body)
        status, _, body = self.request("/api/cases")
        self.assertEqual(200, status)
        cases = json.loads(body)["cases"]
        self.assertEqual(10, len(cases))
        self.assertIn("normal", [case["id"] for case in cases])
        self.assertTrue(all(isinstance(case["turns"], list) for case in cases))
        self.assertTrue(all("expected" not in case for case in cases))
        status, _, body = self.request("/api/config")
        self.assertEqual(200, status)
        config = json.loads(body)
        self.assertEqual("unit-test-model", config["model"])
        self.assertFalse(config["key_configured"])
        self.assertNotIn("api_key", config)
        self.assertEqual([], self.manager.started)

    def test_start_and_poll_contract_and_unknown_run(self):
        status, _, body = self.post({"case_id": "normal", "mode": "offline", "repeat": 1,
                                    "judge": False})
        self.assertEqual(202, status)
        run_id = json.loads(body)["run_id"]
        status, _, body = self.request("/api/runs/" + run_id)
        self.assertEqual(200, status)
        self.assertEqual("running", json.loads(body)["status"])
        status, _, _ = self.request("/api/runs/22222222-2222-4222-8222-222222222222")
        self.assertEqual(404, status)

    def test_case_standards_are_available_before_running_without_credentials(self):
        # Opening a test specification must not load secrets or create a model
        # client. It is an explanation of the fixture, not a paid execution.
        with mock.patch.object(workbench, "load_local_env", side_effect=AssertionError(
                "Viewing standards must not read credentials"), create=True), \
                mock.patch.object(workbench, "ArkClient", side_effect=AssertionError(
                    "Viewing standards must not construct a model client")):
            for case in workbench.CASES:
                with self.subTest(case_id=case["id"]):
                    status, _, body = self.request(f'/api/cases/{case["id"]}/spec')
                    self.assertEqual(200, status)
                    spec = json.loads(body)
                    self.assertEqual(case["turns"], [turn["user"] for turn in spec["turns"]])
                    self.assertEqual(7, len(spec["dimensions"]))
                    self.assertNotIn("api_key", spec)
        self.assertEqual([], self.manager.started)

    def test_unknown_case_spec_is_not_found_and_does_not_start_a_job(self):
        status, _, _ = self.request("/api/cases/no-such-case/spec")
        self.assertEqual(404, status)
        self.assertEqual([], self.manager.started)

    def test_invalid_case_and_busy_error_are_distinct(self):
        self.assertEqual(400, self.post({"case_id": "no-such-case"})[0])
        self.manager.busy = True
        self.assertEqual(409, self.post({"case_id": "normal"})[0])

    def test_external_hosts_and_cross_origin_posts_cannot_trigger_paid_calls(self):
        for headers in (
            {"Origin": "https://example.invalid"},
            {"Origin": "null"},
            {"Origin": f"http://127.0.0.1:{self.port + 1}"},
            {"Host": "example.invalid", "Origin": self.origin},
            {"Host": f"127.0.0.1.example.invalid:{self.port}", "Origin": self.origin},
        ):
            with self.subTest(headers=headers):
                self.assertEqual(403, self.post({"case_id": "normal"}, headers)[0])
        self.assertEqual([], self.manager.started)

    def test_cli_post_without_origin_is_allowed_with_local_host(self):
        status, _, _ = self.request("/api/runs", "POST", '{"case_id":"normal"}',
                                    {"Content-Type": "application/json"})
        self.assertEqual(202, status)

    def test_body_type_json_syntax_and_size_are_checked(self):
        status, _, _ = self.request("/api/runs", "POST", "x", {"Content-Type": "text/plain"})
        self.assertEqual(415, status)
        status, _, _ = self.request("/api/runs", "POST", "{", {"Content-Type": "application/json"})
        self.assertEqual(400, status)
        status, _, _ = self.request("/api/runs", "POST", "x" * (16 * 1024 + 1),
                                    {"Content-Type": "application/json"})
        self.assertEqual(413, status)
        self.assertEqual([], self.manager.started)

    def test_private_and_traversal_paths_are_never_served(self):
        for path in ("/.env", "/.git/config", "/client.py", "/../.env", "/%2e%2e/.env",
                     "/reports/../.env", "/reports/%2e%2e/%2e%2e/.env",
                     "/reports/22222222-2222-4222-8222-222222222222.html"):
            with self.subTest(path=path):
                status, _, _ = self.request(path)
                self.assertIn(status, (400, 403, 404))


class RunManagerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        # The offline workflow must not construct a real API client or read .env.
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(client_module, "load_local_env", side_effect=AssertionError(
            "Server tests must not read local credentials")).start()
        mock.patch.object(workbench, "load_local_env", side_effect=AssertionError(
            "Server tests must not read local credentials"), create=True).start()
        mock.patch.object(workbench, "ArkClient", side_effect=AssertionError(
            "Offline mode must not construct a live API client"), create=True).start()
        mock.patch.object(runner, "pricing_from_env", return_value=None).start()
        self.manager = workbench.RunManager(output_dir=Path(self.directory.name))

    def test_invalid_payloads_are_rejected_before_a_worker_starts(self):
        for payload in (
            [], {}, {"case_id": "unknown"},
            {"case_id": "normal", "mode": "invalid"},
            {"case_id": "normal", "repeat": True},
            {"case_id": "normal", "repeat": "2"},
            {"case_id": "normal", "repeat": 0},
            {"case_id": "normal", "repeat": 4},
            {"case_id": "normal", "judge": "false"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    self.manager.start(payload)

    def test_offline_worker_keeps_fixture_provenance_and_writes_a_real_report(self):
        run_id = self.manager.start({"case_id": "normal", "mode": "offline", "repeat": 2,
                                     "judge": False})
        deadline = time.monotonic() + 5
        snapshot = self.manager.snapshot(run_id)
        while snapshot["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            snapshot = self.manager.snapshot(run_id)
        self.assertEqual("completed", snapshot["status"], snapshot.get("error"))
        self.assertIsNone(snapshot["error"])
        self.assertEqual(2, len(snapshot["results"]))
        for result in snapshot["results"]:
            self.assertEqual("offline_fixture", result["source"])
            self.assertEqual("normal", result["case_id"])
            self.assertEqual(7, len(result["dimensions"]))
            self.assertEqual([], result["model_calls"])
            self.assertEqual(1, sum(result["final_state"]["carts"]["U001"].values()))
        self.assertTrue(snapshot["events"])
        self.assertTrue(snapshot["report_url"].startswith("/reports/"))
        report_files = list(Path(self.directory.name).glob("*.html"))
        self.assertEqual(1, len(report_files))
        html = report_files[0].read_text(encoding="utf-8")
        self.assertIn("offline_fixture", html)
        self.assertIn("正常推荐与加购", html)
        with self.assertRaises(KeyError):
            self.manager.snapshot("missing")

        # Existing files are insufficient: only reports belonging to this server's
        # registered runs may be served, never an arbitrary output-folder file.
        forged_name = "22222222222242228222222222222222.html"
        (Path(self.directory.name) / forged_name).write_text("unregistered report", encoding="utf-8")
        server = workbench.create_server(port=0, manager=self.manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            connection.request("GET", snapshot["report_url"])
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertIn("正常推荐与加购", response.read().decode("utf-8"))
            connection.close()
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            connection.request("GET", "/reports/" + forged_name)
            response = connection.getresponse()
            self.assertEqual(404, response.status)
            response.read()
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
