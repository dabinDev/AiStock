from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = load_module("kanpan_test_server", "kanpan-tool/server.py")
exporter = load_module("kanpan_test_exporter", "market-overview-exporter/scripts/fetch_market_overview.py")
jinji = load_module("kanpan_test_jinji", "duanxian-yidong-pool/scripts/fetch_yidong_pool.py")
launcher = load_module("kanpan_test_launcher", "scripts/run_kanpan_tool.py")
bundle = load_module("kanpan_test_bundle", "scripts/build_kanpan_bundle.py")
shortline = load_module("kanpan_test_shortline", "eastmoney-shortline/scripts/fetch_eastmoney_shortline.py")
shared = load_module("kanpan_test_shared", "duanxian-workflow/scripts/shared.py")


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="astock-test-")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.data = self.workspace / "data"
        self.data.mkdir()
        self.today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        self.addCleanup(patch.stopall)
        patch.object(server, "DATA_ROOT", self.data).start()
        patch.object(server, "CONFIG_ROOT", self.workspace / "config").start()
        patch.object(server, "SETTINGS_FILE", self.workspace / "config/settings.json").start()
        with server.JOB_LOCK:
            server.JOBS.clear()

    def put(self, target: str, day: str | None = None, content: str = "# 测试数据\n\n| 名称 | 代码 |\n| --- | --- |\n| 示例 | 000001 |\n") -> Path:
        path = self.data / (day or self.today) / server.TARGETS[target]["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8-sig")
        return path

    def wait_job(self, job_id: str):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            snapshot = server.snapshot_job(job_id)
            if snapshot["status"] != "running":
                return snapshot
            time.sleep(0.01)
        self.fail("Background job did not finish")

    @contextmanager
    def api(self):
        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        opener = build_opener(ProxyHandler({}))

        def request(path, payload=None, *, raw=False):
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            req = Request(f"http://127.0.0.1:{httpd.server_port}{path}", data=body, headers={"Content-Type": "application/json"})
            try:
                with opener.open(req, timeout=3) as response:
                    body = response.read()
                    if raw == "bytes":
                        return response.status, {"body": body, "content_type": response.headers.get("Content-Type")}
                    text = body.decode("utf-8")
                    return response.status, ({"text": text, "content_type": response.headers.get("Content-Type")} if raw else json.loads(text))
            except HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        try:
            yield request
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


class DateAndApiTests(WorkspaceTest):
    def test_default_date_is_today_even_with_old_archives(self):
        self.put("jingjia", "2026-07-24")
        with self.api() as request:
            status, body = request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(body["currentDate"], self.today)
        self.assertIn(self.today, body["dates"])
        self.assertEqual(body["availableTargets"], [])

    def test_archived_date_can_still_be_selected(self):
        self.put("jingjia", "2026-07-24")
        with self.api() as request:
            status, body = request("/api/state?date=2026-07-24")
        self.assertEqual(status, 200)
        self.assertEqual(body["currentDate"], "2026-07-24")
        self.assertEqual(body["availableTargets"], ["jingjia"])

    def test_impossible_calendar_date_is_rejected(self):
        with self.assertRaises(ValueError):
            server.normalize_date("2026-02-30")

    def test_invalid_date_has_json_error_response(self):
        with self.api() as request:
            status, body = request("/api/state?date=2026-02-30")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_historical_live_fetch_is_rejected_before_starting(self):
        self.put("jjyd", "2026-07-24")
        with self.api() as request, patch.object(server, "start_fetch_job") as start:
            status, body = request("/api/fetch", {"date": "2026-07-24", "targets": ["jingjia", "jjyd"]})
        self.assertEqual(status, 400)
        self.assertIn(self.today, body["error"])
        start.assert_not_called()

    def test_future_fetch_is_rejected(self):
        with self.api() as request, patch.object(server, "start_fetch_job") as start:
            status, _ = request("/api/fetch", {"date": "2099-01-01", "targets": ["jingjia"]})
        self.assertEqual(status, 400)
        start.assert_not_called()

    def test_unknown_target_has_json_error_response(self):
        with self.api() as request:
            status, body = request("/api/fetch", {"date": self.today, "targets": ["invalid"]})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_explicit_empty_selection_does_not_fetch_everything(self):
        with self.api() as request, patch.object(server, "start_fetch_job") as start:
            status, _ = request("/api/fetch", {"date": self.today, "targets": []})
        self.assertEqual(status, 400)
        start.assert_not_called()

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            server.read_file(self.today, "../../README.md")

    def test_saved_key_is_masked_and_preserved(self):
        fake_key = "test-only-key-123456789"
        with self.api() as request:
            status, body = request("/api/settings", {"kimi_key": fake_key, "kimi_model": "test-model"})
            self.assertEqual(status, 200)
            self.assertTrue(body["hasKimiKey"])
            self.assertNotIn(fake_key, json.dumps(body))
            status, body = request("/api/settings", {"kimi_key": "", "kimi_model": "test-model-2", "keep_existing_key": True})
            self.assertEqual(status, 200)
        self.assertEqual(server.load_settings()["kimi_key"], fake_key)
        self.assertEqual(body["kimiModel"], "test-model-2")


class ModelConfigurationTests(WorkspaceTest):
    def test_deep_upstream_http_error_matrix_is_redacted_and_never_retried(self):
        key = "test-private-key"
        with self.api() as request:
            for path, method in [("/api/models", "get"), ("/api/model-test", "post")]:
                for code in [401, 403, 404, 429, 500, 503]:
                    with self.subTest(path=path, code=code):
                        response = Mock(status_code=code, text=f'{{"error":"upstream rejected {key}"}}')
                        response.raise_for_status.side_effect = server.requests.HTTPError(response=response)
                        with patch.object(server.requests, method, return_value=response) as network:
                            status, body = request(path, {"api_key": key, "model": "test-model"})
                        self.assertEqual(status, 502)
                        self.assertIn(f"HTTP {code}", body["error"])
                        self.assertNotIn(key, json.dumps(body))
                        network.assert_called_once()
                        response.close.assert_called_once()

    def test_deep_network_disconnects_and_timeouts_are_not_retried(self):
        with self.api() as request:
            for path, method in [("/api/models", "get"), ("/api/model-test", "post")]:
                for error in [server.requests.Timeout("timeout"), server.requests.ConnectionError("connection reset")]:
                    with self.subTest(path=path, error=type(error).__name__):
                        with patch.object(server.requests, method, side_effect=error) as network:
                            status, body = request(path, {"api_key": "test-key", "model": "test-model"})
                        self.assertEqual(status, 502)
                        self.assertTrue(body["error"])
                        network.assert_called_once()

    def test_deep_non_object_requests_do_not_reach_upstream(self):
        with self.api() as request, patch.object(server.requests, "get") as get, patch.object(server.requests, "post") as post:
            for path in ["/api/models", "/api/model-test"]:
                for payload in [[], ["model"], 42, "plain text"]:
                    with self.subTest(path=path, payload=payload):
                        status, body = request(path, payload)
                        self.assertEqual(status, 400)
                        self.assertIn("JSON", body["error"])
        get.assert_not_called()
        post.assert_not_called()

    def test_deep_invalid_upstream_json_preserves_configuration_and_closes_response(self):
        server.save_settings("test-saved-key", "saved-model")
        before = server.SETTINGS_FILE.read_bytes()
        with self.api() as request:
            for path, method in [("/api/models", "get"), ("/api/model-test", "post")]:
                response = Mock(headers={"Content-Type": "application/json"})
                response.json.side_effect = ValueError("invalid upstream JSON")
                with patch.object(server.requests, method, return_value=response):
                    status, body = request(path, {"model": "test-model"})
                self.assertEqual(status, 502)
                self.assertIn("invalid upstream JSON", body["error"])
                response.close.assert_called_once()
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)

    def test_deep_model_probe_handles_reasoning_usage_and_chinese_stream(self):
        response = Mock(headers={"Content-Type": "text/event-stream"})
        response.iter_lines.return_value = [
            ': heartbeat', '',
            'data: {"choices":[{"delta":{"reasoning_content":"hidden reasoning"}}]}',
            'data: {"choices":[{"delta":{"content":"测"}}]}',
            'data: {"choices":[{"delta":{"content":"试通过"},"finish_reason":"stop"}]}',
            'data: {"choices":[],"usage":{"total_tokens":10}}',
            'data: [DONE]',
        ]
        with self.api() as request, patch.object(server.requests, "post", return_value=response) as post:
            status, body = request("/api/model-test", {"api_key": "test-key", "model": "test-model"})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "测试通过")
        self.assertNotIn("reasoning", json.dumps(body))
        self.assertFalse(server.SETTINGS_FILE.exists())
        post.assert_called_once()
        response.close.assert_called_once()

    def test_deep_incomplete_streams_never_report_probe_success(self):
        streams = [
            ['data: {"choices":[{"delta":{"content":"partial"}}]}'],
            ['data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}'],
            ['data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"content_filter"}]}'],
            ['data: {"error":{"message":"test-private-key rejected"}}'],
            ['data: not-json'],
            ['data: [DONE]'],
        ]
        with self.api() as request:
            for lines in streams:
                with self.subTest(lines=lines):
                    response = Mock(headers={"Content-Type": "text/event-stream"})
                    response.iter_lines.return_value = lines
                    with patch.object(server.requests, "post", return_value=response) as post:
                        status, body = request("/api/model-test", {"api_key": "test-private-key", "model": "test-model"})
                    self.assertEqual(status, 502)
                    self.assertNotIn("test-private-key", json.dumps(body))
                    self.assertNotIn("ok", body)
                    post.assert_called_once()
                    response.close.assert_called_once()

    def test_deep_failed_settings_write_preserves_previous_configuration(self):
        server.save_settings("test-saved-key", "original-model")
        before = server.SETTINGS_FILE.read_bytes()
        original_write = Path.write_text

        def partial_write(path, content, *args, **kwargs):
            if path.parent == server.CONFIG_ROOT:
                with path.open("w", encoding="utf-8") as file:
                    file.write(content[:10])
                raise OSError("simulated disk write failure")
            return original_write(path, content, *args, **kwargs)

        with patch.object(Path, "write_text", new=partial_write):
            with self.assertRaises(OSError):
                server.save_settings("", "new-model", keep_existing_key=True)
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)
        self.assertEqual(list(server.CONFIG_ROOT.glob("*.tmp")), [])

    def test_deep_failed_atomic_replace_preserves_previous_configuration(self):
        server.save_settings("test-saved-key", "original-model")
        before = server.SETTINGS_FILE.read_bytes()
        with patch.object(server.os, "replace", side_effect=OSError("simulated rename failure")):
            with self.assertRaises(OSError):
                server.save_settings("", "new-model", keep_existing_key=True)
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)
        self.assertEqual(list(server.CONFIG_ROOT.glob("*.tmp")), [])

    def test_deep_parallel_settings_reads_and_saves_preserve_key(self):
        server.save_settings("test-saved-key", "initial")
        barrier = threading.Barrier(6)
        errors = []

        def writer(number):
            barrier.wait()
            try:
                for index in range(25):
                    server.save_settings("", f"model-{number}-{index}", keep_existing_key=True)
            except Exception as error:
                errors.append(type(error).__name__)

        def reader():
            barrier.wait()
            try:
                for _ in range(75):
                    settings = server.load_settings()
                    if settings["kimi_key"] != "test-saved-key":
                        errors.append("saved key missing during concurrent read")
            except Exception as error:
                errors.append(type(error).__name__)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(2)] + [threading.Thread(target=reader) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(server.load_settings()["kimi_key"], "test-saved-key")

    def models_response(self, rows):
        response = Mock()
        response.json.return_value = {"data": rows}
        return response

    def reply_response(self, content="OK"):
        response = Mock(headers={"Content-Type": "application/json"})
        response.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
        return response

    def test_model_list_is_authenticated_sorted_and_deduplicated(self):
        response = self.models_response([{"id": "kimi-b"}, {"id": "kimi-a"}, {"id": "kimi-a"}, {"id": " "}, {"other": "ignored"}])
        with self.api() as request, patch.object(server.requests, "get", return_value=response) as get:
            status, body = request("/api/models", {"api_key": "test-only-key"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"models": ["kimi-a", "kimi-b"]})
        self.assertEqual(get.call_args.args[0], server.KIMI_MODELS_URL)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-only-key")
        self.assertFalse(server.SETTINGS_FILE.exists())
        response.close.assert_called_once()

    def test_model_listing_uses_saved_key_without_changing_settings(self):
        server.save_settings("test-saved-key", "saved-model")
        before = server.SETTINGS_FILE.read_bytes()
        with self.api() as request, patch.object(server.requests, "get", return_value=self.models_response([{"id": "new-model"}])) as get:
            status, _ = request("/api/models", {})
        self.assertEqual(status, 200)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-saved-key")
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)

    def test_unsaved_key_takes_precedence_for_model_operations(self):
        server.save_settings("test-saved-key", "saved-model")
        before = server.SETTINGS_FILE.read_bytes()
        with self.api() as request, patch.object(server.requests, "get", return_value=self.models_response([{"id": "model"}])) as get:
            status, _ = request("/api/models", {"api_key": "test-transient-key"})
        self.assertEqual(status, 200)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-transient-key")
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)

    def test_model_operations_can_use_environment_key(self):
        with self.api() as request, patch.dict(server.os.environ, {"MOONSHOT_API_KEY": "test-env-key"}), patch.object(server.requests, "get", return_value=self.models_response([{"id": "model"}])) as get:
            status, _ = request("/api/models", {})
        self.assertEqual(status, 200)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-env-key")

    def test_missing_key_is_rejected_before_model_network_request(self):
        with self.api() as request, patch.dict(server.os.environ, {"MOONSHOT_API_KEY": ""}), patch.object(server.requests, "get") as get, patch.object(server.requests, "post") as post:
            for path in ["/api/models", "/api/model-test"]:
                status, body = request(path, {"model": "test-model"})
                self.assertEqual(status, 400)
                self.assertIn("API Key", body["error"])
        get.assert_not_called()
        post.assert_not_called()

    def test_invalid_model_or_key_is_rejected_before_network_request(self):
        with self.api() as request, patch.object(server.requests, "post") as post:
            for payload in [{"api_key": "test-key", "model": ""}, {"api_key": "test-key", "model": 123}, {"api_key": 0, "model": "test-model"}]:
                status, _ = request("/api/model-test", payload)
                self.assertEqual(status, 400)
        post.assert_not_called()

    def test_upstream_auth_error_is_redacted_and_not_retried(self):
        key = "test-only-key"
        response = Mock(status_code=401, text=f"Invalid Authentication: {key}")
        response.raise_for_status.side_effect = server.requests.HTTPError(response=response)
        with self.api() as request, patch.object(server.requests, "get", return_value=response) as get:
            status, body = request("/api/models", {"api_key": key})
        self.assertEqual(status, 502)
        self.assertIn("HTTP 401", body["error"])
        self.assertNotIn(key, json.dumps(body))
        get.assert_called_once()
        response.close.assert_called_once()

    def test_empty_or_malformed_model_list_is_not_reported_as_success(self):
        for data in [{"data": []}, {"data": "invalid"}, {"unexpected": []}]:
            with self.subTest(data=data):
                response = Mock()
                response.json.return_value = data
                with patch.object(server.requests, "get", return_value=response):
                    with self.assertRaises(RuntimeError):
                        server.list_kimi_models("test-only-key")
                response.close.assert_called_once()

    def test_test_selected_model_sends_only_a_short_message_and_does_not_save(self):
        server.save_settings("test-saved-key", "saved-model")
        before = server.SETTINGS_FILE.read_bytes()
        response = self.reply_response()
        with self.api() as request, patch.object(server.requests, "post", return_value=response) as post:
            status, body = request("/api/model-test", {"api_key": "test-transient-key", "model": "chosen-model"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["model"], "chosen-model")
        self.assertEqual(body["reply"], "OK")
        self.assertGreaterEqual(body["elapsed_ms"], 0)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "chosen-model")
        self.assertLess(len(json.dumps(payload["messages"], ensure_ascii=False)), 100)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-transient-key")
        self.assertEqual(server.SETTINGS_FILE.read_bytes(), before)
        post.assert_called_once()
        response.close.assert_called_once()

    def test_model_test_timeout_does_not_retry(self):
        with self.api() as request, patch.object(server.requests, "post", side_effect=server.requests.Timeout("timeout")) as post:
            status, body = request("/api/model-test", {"api_key": "test-only-key", "model": "test-model"})
        self.assertEqual(status, 502)
        self.assertIn("超时", body["error"])
        post.assert_called_once()
        self.assertFalse(server.SETTINGS_FILE.exists())

    def test_model_test_has_shorter_stream_deadline(self):
        response = Mock(headers={"Content-Type": "text/event-stream"})
        response.iter_lines.return_value = ['data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}']
        with patch.object(server.requests, "post", return_value=response), patch.object(server.time, "monotonic", side_effect=[0, 0, 61]):
            with self.assertRaisesRegex(RuntimeError, "超过 60 秒"):
                server.test_kimi_model("test-only-key", "test-model")
        response.close.assert_called_once()

    def test_model_test_rejects_empty_reply(self):
        with self.api() as request, patch.object(server.requests, "post", return_value=self.reply_response("")):
            status, body = request("/api/model-test", {"api_key": "test-only-key", "model": "test-model"})
        self.assertEqual(status, 502)
        self.assertIn("未返回分析正文", body["error"])

    def test_saved_model_is_restored_without_losing_saved_key(self):
        server.save_settings("test-saved-key", "old-model")
        with self.api() as request:
            status, _ = request("/api/settings", {"kimi_key": "", "kimi_model": "chosen-model", "keep_existing_key": True})
            self.assertEqual(status, 200)
            status, body = request("/api/settings")
        self.assertEqual(status, 200)
        self.assertEqual(body["kimiModel"], "chosen-model")
        self.assertTrue(body["hasKimiKey"])
        self.assertEqual(server.load_settings()["kimi_key"], "test-saved-key")

    def test_real_local_http_model_selection_test_and_save_flow(self):
        upstream_calls = []

        class Upstream(server.BaseHTTPRequestHandler):
            def do_GET(self):
                upstream_calls.append(("GET", self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": "model-a"}, {"id": "model-b"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                upstream_calls.append(("POST", self.path, payload["model"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                self.wfile.flush()

            def log_message(self, *args):
                pass

        upstream = server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{upstream.server_port}/v1"
        try:
            with self.api() as request, patch.object(server, "KIMI_MODELS_URL", base + "/models"), patch.object(server, "KIMI_BASE_URL", base + "/chat/completions"):
                status, _ = request("/api/settings", {"kimi_key": "local-test-key", "kimi_model": "model-a"})
                self.assertEqual(status, 200)
                status, models = request("/api/models", {})
                self.assertEqual((status, models["models"]), (200, ["model-a", "model-b"]))
                status, tested = request("/api/model-test", {"model": "model-b"})
                self.assertEqual((status, tested["reply"], tested["model"]), (200, "OK", "model-b"))
                self.assertEqual(server.load_settings()["kimi_model"], "model-a")
                status, _ = request("/api/settings", {"kimi_model": "model-b", "keep_existing_key": True})
                self.assertEqual(status, 200)
                status, saved = request("/api/settings")
                self.assertEqual((status, saved["kimiModel"], saved["hasKimiKey"]), (200, "model-b", True))
            self.assertEqual(upstream_calls, [("GET", "/v1/models", "Bearer local-test-key"), ("POST", "/v1/chat/completions", "model-b")])
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=3)


class JobTests(WorkspaceTest):
    def fake_process(self, failed=(), missing=()):
        calls = []

        def start(command, **kwargs):
            calls.append(command)
            if "--rebuild-only" in command:
                return SimpleNamespace(stdout=io.StringIO("indexes rebuilt\n"), wait=lambda: 0)
            target = command[command.index("--targets") + 1]
            if target not in failed and target not in missing:
                self.put(target)
            return SimpleNamespace(stdout=io.StringIO(f"{target}\n"), wait=lambda: 1 if target in failed else 0)

        return start, calls

    def test_parallel_fetch_rebuilds_indexes_once(self):
        process, calls = self.fake_process()
        with patch.object(server.subprocess, "Popen", side_effect=process):
            result = self.wait_job(server.start_fetch_job(self.today, ["jingjia", "jjyd"]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["fetched_count"], 2)
        self.assertEqual(sum("--rebuild-only" in command for command in calls), 1)
        self.assertTrue(all("--skip-index" in command for command in calls if "--targets" in command))

    def test_failed_fetch_does_not_count_old_file_as_success(self):
        self.put("jjyd", content="# 已归档旧内容\n")
        process, _ = self.fake_process(failed={"jjyd"})
        with patch.object(server.subprocess, "Popen", side_effect=process):
            result = self.wait_job(server.start_fetch_job(self.today, ["jingjia", "jjyd"]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["result"]["fetched_count"], 1)
        item = next(item for item in result["result"]["target_data"] if item["target"] == "jjyd")
        self.assertEqual(item["fetch_status"], "failed")
        self.assertEqual(item["exists"], "true")
        self.assertIn("已归档旧内容", (self.data / self.today / "jjyd.md").read_text(encoding="utf-8-sig"))

    def test_zero_exit_without_output_is_not_success(self):
        process, _ = self.fake_process(missing={"jingjia"})
        with patch.object(server.subprocess, "Popen", side_effect=process):
            result = self.wait_job(server.start_fetch_job(self.today, ["jingjia"]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["target_statuses"]["jingjia"], "failed")

    def test_analysis_saves_result_with_mock_upstream(self):
        self.put("jingjia")
        with patch.object(server, "call_kimi", return_value="10-19 09:25\n\n【竞价总览】\n测试结论。") as call:
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-only-key", "test-model", preset="open"))
        self.assertEqual(result["status"], "completed")
        call.assert_called_once()
        self.assertTrue((self.data / self.today / result["result"]["analysis_file"]).is_file())
        self.assertIn("【竞价总览】", result["result"]["analysis"])

    def test_analysis_without_key_does_not_call_upstream(self):
        with patch.dict(server.os.environ, {"MOONSHOT_API_KEY": ""}), patch.object(server, "call_kimi") as call:
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "", "test-model"))
        self.assertEqual(result["status"], "failed")
        call.assert_not_called()

    def test_missing_prompt_data_is_explicit(self):
        with self.assertRaisesRegex(RuntimeError, "尚未抓取成功"):
            server.build_prompt_payload(self.today, ["jingjia"])

    def test_close_prompt_uses_expected_aliases(self):
        for target in ["global", "pool", "amount"]:
            self.put(target)
        prompt = server.build_prompt_payload(self.today, ["global", "pool", "amount"], preset="close")["prompt"]
        for alias in ["[zhishu]", "[ztguchi]", "[chengjiao]"]:
            self.assertIn(alias, prompt)

    def test_prompt_compaction_keeps_all_modules(self):
        prompt = "分析约束\n" + "\n".join(f"### 数据模块：{target}\n" + "数据" * 6000 for target in server.TARGET_ORDER)
        compact = server.compact_prompt_for_kimi(prompt)
        self.assertLessEqual(len(compact), 24000)
        for target in server.TARGET_ORDER:
            self.assertIn(f"### 数据模块：{target}", compact)

    def test_analysis_failure_does_not_save_partial_result(self):
        self.put("jingjia")
        with patch.object(server, "call_kimi", side_effect=RuntimeError("连接提前结束")):
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-only-key", "test-model"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["target_statuses"]["jingjia"], "failed")
        self.assertEqual(result["result"]["analysis_file"], "")
        self.assertEqual(list((self.data / self.today).glob("kimi-analysis-*.md")), [])


class KimiTests(unittest.TestCase):
    def stream_response(self, events):
        response = Mock()
        response.headers = {"Content-Type": "text/event-stream; charset=utf-8"}
        response.iter_lines.return_value = [
            "data: " + (event if isinstance(event, str) else json.dumps(event, ensure_ascii=False))
            for event in events
        ]
        return response

    def test_stream_separates_reasoning_and_body_and_accepts_usage_chunk(self):
        response = self.stream_response([
            {"choices": [{"delta": {"reasoning_content": "不应展示的思考内容"}}]},
            {"choices": [{"delta": {"content": "## 中文分析\n"}}]},
            {"choices": [{"delta": {"content": "数据完整。"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 123}},
            "[DONE]",
        ])
        progress = []
        with patch.object(server.requests, "post", return_value=response) as post:
            result = server.call_kimi("test-only-key", "test-model", "test prompt", on_progress=progress.append)
        self.assertEqual(result, "## 中文分析\n数据完整。")
        self.assertIn("模型正在思考", progress)
        self.assertIn("正在生成分析正文", progress)
        self.assertNotIn("不应展示", "".join(progress))
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertTrue(post.call_args.kwargs["json"]["stream"])
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 90))
        response.close.assert_called_once()

    def test_timeout_does_not_retry_or_resend_compacted_prompt(self):
        with patch.object(server.requests, "post", side_effect=server.requests.Timeout("test timeout")) as post:
            with self.assertRaisesRegex(RuntimeError, "响应超时"):
                server.call_kimi("test-only-key", "test-model", "数据" * 15000)
        post.assert_called_once()

    def test_auth_failure_is_reported_once_and_redacts_key(self):
        key = "test-only-key"
        response = Mock(status_code=401, text=f'{{"error":"Invalid Authentication {key}"}}')
        response.raise_for_status.side_effect = server.requests.HTTPError(response=response)
        with patch.object(server.requests, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as error:
                server.call_kimi(key, "test-model", "数据" * 15000)
        self.assertNotIn(key, str(error.exception))
        post.assert_called_once()
        response.close.assert_called_once()

    def test_explicit_context_rejection_can_use_compacted_prompt(self):
        rejected = Mock(status_code=400, text='{"error":"maximum context length exceeded"}')
        rejected.raise_for_status.side_effect = server.requests.HTTPError(response=rejected)
        accepted = self.stream_response([{"choices": [{"delta": {"content": "压缩后的分析"}, "finish_reason": "stop"}]}, "[DONE]"])
        prompt = "数据" * 15000
        with patch.object(server.requests, "post", side_effect=[rejected, accepted]) as post:
            result = server.call_kimi("test-only-key", "test-model", prompt)
        self.assertEqual(result, "压缩后的分析")
        self.assertEqual(post.call_count, 2)
        second_prompt = post.call_args_list[1].kwargs["json"]["messages"][-1]["content"]
        self.assertLessEqual(len(second_prompt), 24000)
        rejected.close.assert_called_once()
        accepted.close.assert_called_once()

    def test_invalid_or_incomplete_streams_fail_without_retry(self):
        cases = [
            (["[DONE]"], "未返回分析正文"),
            ([{"choices": [{"delta": {"content": "不完整"}}]}], "连接提前结束"),
            ([{"choices": [{"delta": {"content": "不完整"}, "finish_reason": "length"}]}], "未完整生成"),
            ([{"error": {"message": "upstream unavailable"}}], "返回错误"),
            (["not-json"], "Expecting value"),
        ]
        for events, message in cases:
            with self.subTest(message=message):
                response = self.stream_response(events)
                with patch.object(server.requests, "post", return_value=response) as post:
                    with self.assertRaisesRegex(RuntimeError, message):
                        server.call_kimi("test-only-key", "test-model", "test prompt")
                post.assert_called_once()
                response.close.assert_called_once()

    def test_stream_has_total_time_limit(self):
        response = self.stream_response([{"choices": [{"delta": {"reasoning_content": "thinking"}}]}])
        with patch.object(server.requests, "post", return_value=response), patch.object(server.time, "monotonic", side_effect=[0, 901]):
            with self.assertRaisesRegex(RuntimeError, "超过 900 秒"):
                server.call_kimi("test-only-key", "test-model", "test prompt")
        response.close.assert_called_once()

    def test_active_long_reasoning_is_not_interrupted_at_five_minutes(self):
        response = self.stream_response([
            {"choices": [{"delta": {"reasoning_content": "模拟长时间思考"}}]},
            {"choices": [{"delta": {"content": "完整的最终分析"}, "finish_reason": "stop"}]},
            "[DONE]",
        ])
        with patch.object(server.requests, "post", return_value=response), patch.object(server.time, "monotonic", side_effect=[0, 301, 400, 401]):
            self.assertEqual(server.call_kimi("test-only-key", "test-model", "test prompt"), "完整的最终分析")
        response.close.assert_called_once()

    def test_json_response_fallback_requires_nonempty_complete_content(self):
        for content, finish_reason, succeeds in [("完整分析", "stop", True), ("", "stop", False), ("截断分析", "length", False)]:
            with self.subTest(content=content):
                response = Mock(headers={"Content-Type": "application/json"})
                response.json.return_value = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
                with patch.object(server.requests, "post", return_value=response):
                    if succeeds:
                        self.assertEqual(server.call_kimi("test-only-key", "test-model", "test prompt"), content)
                    else:
                        with self.assertRaises(RuntimeError):
                            server.call_kimi("test-only-key", "test-model", "test prompt")
                response.close.assert_called_once()

    def test_real_local_http_stream_preserves_chinese_chunks(self):
        received = []

        class Handler(server.BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in [
                    {"choices": [{"delta": {"reasoning_content": "模拟思考"}}]},
                    {"choices": [{"delta": {"content": "## 本地验证\n"}}]},
                    {"choices": [{"delta": {"content": "中文分块完整。"}, "finish_reason": "stop"}]},
                    "[DONE]",
                ]:
                    payload = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()

            def log_message(self, *args):
                pass

        httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(server, "KIMI_BASE_URL", f"http://127.0.0.1:{httpd.server_port}/chat/completions"):
                result = server.call_kimi("local-test-only", "test-model", "本地请求")
            self.assertEqual(result, "## 本地验证\n中文分块完整。")
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0]["stream"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


class ExporterTests(WorkspaceTest):
    def test_exporter_rejects_invalid_calendar_date(self):
        with self.assertRaises(SystemExit):
            exporter.normalize_date("2026-02-30")

    def test_historical_live_cli_fails_before_running_collectors(self):
        with patch.object(sys, "argv", ["fetch", "--date", "2026-07-24", "--targets", "global", "--output-root", str(self.data)]), patch.object(exporter, "export_existing_targets", return_value=[]) as collect, patch.object(exporter, "collect_with_playwright", return_value=[]):
            with self.assertRaises(SystemExit):
                exporter.main()
        collect.assert_not_called()

    def test_incremental_navigation_keeps_all_existing_modules(self):
        self.put("jingjia")
        self.put("jjyd")
        exporter.rebuild_day_indexes(self.data, self.today)
        meta = exporter.read_dashboard_meta(self.data / self.today / "dashboard.md")
        self.assertEqual(meta["targets"], ["jingjia", "jjyd"])
        self.put("amount")
        exporter.rebuild_day_indexes(self.data, self.today)
        meta = exporter.read_dashboard_meta(self.data / self.today / "dashboard.md")
        self.assertEqual(meta["targets"], ["jingjia", "jjyd", "amount"])

    def test_rebuild_only_never_runs_browser_collectors(self):
        self.put("jingjia", "2026-07-24")
        with patch.object(sys, "argv", ["fetch", "--date", "2026-07-24", "--rebuild-only", "--output-root", str(self.data)]), patch.object(exporter, "export_existing_targets") as collect, patch.object(exporter, "collect_with_playwright") as browser, redirect_stdout(io.StringIO()):
            self.assertEqual(exporter.main(), 0)
        collect.assert_not_called()
        browser.assert_not_called()
        self.assertTrue((self.data / "2026-07-24/dashboard.md").is_file())

    def test_skip_index_does_not_overwrite_navigation(self):
        file = self.put("jingjia")
        results = [exporter.ExportResult("jingjia", file)]
        with patch.object(sys, "argv", ["fetch", "--date", self.today, "--targets", "jingjia", "--skip-index", "--output-root", str(self.data)]), patch.object(exporter, "export_existing_targets", return_value=results), patch.object(exporter, "collect_with_playwright", return_value=[]), patch.object(exporter, "rebuild_day_indexes") as rebuild, redirect_stdout(io.StringIO()):
            self.assertEqual(exporter.main(), 0)
        rebuild.assert_not_called()

    def test_jinji_metadata_survives_output_cleaning_and_summary(self):
        snapshot = jinji.JinjiSnapshot(self.today, self.today, "https://example.invalid/jinjidata.json", 1, 1, {"成功": 1}, [jinji.JinjiStage("1进2", "1/1", [jinji.JinjiEntry("1进2", "1/1", "SZ", "测试", "000001", "成功", "up", "10%", "测试题材")])])
        path = self.data / self.today / "jinji.md"
        jinji.write_text(path, jinji.build_jinji_markdown(snapshot))
        meta = jinji.read_meta(path, jinji.JINJI_META_PREFIX)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["entry_count"], 1)
        summary = jinji.rebuild_summary(self.data, "summary.md", "yidong.md", "pool.md", "jinji.md")
        self.assertIn(f"./{self.today}/jinji.md", summary.read_text(encoding="utf-8-sig"))

    def test_fupan_reports_source_date_instead_of_snapshot_date(self):
        page = Mock()

        def evaluate(script):
            if "#fdate" in script:
                return "2026-10-16"
            if "table.ztlist" in script:
                return [{"tableId": "ztlist_1", "headers": ["名称", "代码"], "rows": [["示例", "000001"]]}]
            if "#plates" in script:
                return [["测试板块", "100", "1"]]
            return [{"name": "ZT", "text": "涨停家数：1"}]

        page.evaluate.side_effect = evaluate
        with patch.object(exporter, "wait_page_ready"):
            markdown, count = exporter.collect_fupan(page)
        self.assertIn("数据日期：2026-10-16", markdown)
        self.assertIn(f"快照日期：{self.today}", markdown)
        self.assertEqual(count, 1)


class LauncherAndShortlineTests(unittest.TestCase):
    def test_bundle_preserves_prompt_templates_at_expected_runtime_paths(self):
        with tempfile.TemporaryDirectory(prefix="astock-bundle-") as temporary:
            folder = Path(temporary)
            separator = ";" if bundle.os.name == "nt" else ":"
            for source in bundle.RUNTIME_FILES:
                source_text, destination = bundle.add_data_arg(source).split(separator, 1)
                target = folder / destination / Path(source_text).name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            system_paths = {preset: folder / path.name for preset, path in server.PROMPT_SYSTEM_FILES.items()}
            with patch.object(server, "PROMPT_REQUIREMENTS_FILE", folder / server.PROMPT_REQUIREMENTS_FILE.name), patch.object(server, "PROMPT_SYSTEM_FILES", system_paths):
                server.read_prompt_template.cache_clear()
                try:
                    self.assertEqual(server.prompt_requirements_text(), (ROOT / "解析提示词要求.md").read_text(encoding="utf-8").strip())
                    for preset, path in system_paths.items():
                        self.assertEqual(server.prompt_system_text(preset), (ROOT / path.name).read_text(encoding="utf-8").strip())
                finally:
                    server.read_prompt_template.cache_clear()

    def test_source_launcher_passes_entry_script(self):
        with patch.object(launcher, "runtime_env", return_value={}), patch.object(launcher, "app_root", return_value=ROOT), patch.object(launcher.subprocess, "Popen") as popen, patch.object(sys, "frozen", False, create=True):
            launcher.spawn_server(8767)
        command = popen.call_args.args[0]
        self.assertEqual(command, [sys.executable, str(ROOT / "scripts/run_kanpan_tool.py")])

    def test_frozen_launcher_executes_binary_without_python_script(self):
        with patch.object(launcher, "runtime_env", return_value={}), patch.object(launcher, "app_root", return_value=ROOT), patch.object(launcher.subprocess, "Popen") as popen, patch.object(sys, "frozen", True, create=True):
            launcher.spawn_server(8767)
        self.assertEqual(popen.call_args.args[0], [sys.executable])

    def test_frozen_launcher_passes_portable_browser_to_server(self):
        with tempfile.TemporaryDirectory(prefix="astock-portable-") as temporary:
            folder = Path(temporary)
            browser = folder / "browser/chrome.exe"
            browser.parent.mkdir()
            browser.touch()
            executable = str(folder / "AstockKanpan.exe")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", executable), patch.object(launcher, "app_root", return_value=folder), patch.dict(launcher.os.environ, {"PLAYWRIGHT_CHROME_EXECUTABLE": ""}), patch.object(launcher.subprocess, "Popen") as popen:
                launcher.spawn_server(8767)
            env = popen.call_args.kwargs["env"]
            self.assertEqual(env["PLAYWRIGHT_CHROME_EXECUTABLE"], str(browser))
            self.assertEqual(env["ASTOCK_SERVER_MODE"], "1")
            self.assertEqual(env["KANPAN_PORT"], "8767")

    def test_portable_browser_does_not_replace_custom_browser(self):
        with tempfile.TemporaryDirectory(prefix="astock-portable-") as temporary:
            folder = Path(temporary)
            browser = folder / "browser/chrome.exe"
            browser.parent.mkdir()
            browser.touch()
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(folder / "AstockKanpan.exe")), patch.object(launcher, "app_root", return_value=folder), patch.dict(launcher.os.environ, {"PLAYWRIGHT_CHROME_EXECUTABLE": "custom-browser.exe"}):
                env = launcher.runtime_env()
            self.assertEqual(env["PLAYWRIGHT_CHROME_EXECUTABLE"], "custom-browser.exe")

    def test_missing_portable_browser_keeps_existing_browser_fallback(self):
        with tempfile.TemporaryDirectory(prefix="astock-portable-") as temporary:
            folder = Path(temporary)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(folder / "AstockKanpan.exe")), patch.object(launcher, "app_root", return_value=folder), patch.dict(launcher.os.environ, {"PLAYWRIGHT_CHROME_EXECUTABLE": ""}):
                self.assertEqual(launcher.runtime_env().get("PLAYWRIGHT_CHROME_EXECUTABLE", ""), "")

    def test_shortline_jsonp_and_field_units(self):
        self.assertEqual(shortline.parse_jsonp('callback({"data":{"tc":1}});')["data"]["tc"], 1)
        self.assertEqual(shortline.format_price(12340), "12.34")
        self.assertEqual(shortline.format_amount(123000000), "1.23亿")
        self.assertEqual(shortline.format_time_value(92501), "09:25:01")
        self.assertEqual(shortline.format_lianban(1), "首板")

    def test_shortline_pagination_preserves_all_rows(self):
        pages = [[{"c": f"{i:06d}"} for i in range(20)], [{"c": f"{i:06d}"} for i in range(20, 23)]]
        responses = [SimpleNamespace(ok=True, text=lambda rows=rows: "cb(" + json.dumps({"data": {"tc": 23, "pool": rows}}) + ");") for rows in pages]
        context = Mock()
        context.get.side_effect = responses
        rows = shortline.fetch_pool(context, shortline.POOL_CONFIGS[0], "20261019")
        self.assertEqual(len(rows), 23)
        self.assertEqual(len({row["c"] for row in rows}), 23)
        self.assertEqual(context.get.call_count, 2)

    def test_trading_phase_boundaries(self):
        for clock, expected in [("09:29", "auction"), ("09:30", "intraday"), ("14:59", "intraday"), ("15:00", "after-close")]:
            current = datetime.fromisoformat("2026-10-19T" + clock).replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            self.assertEqual(shared.detect_auto_phase(current), expected)


@unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend function checks")
class FrontendTests(unittest.TestCase):
    def test_complete_frontend_script_has_valid_syntax(self):
        html = (ROOT / "kanpan-tool/index.html").read_text(encoding="utf-8-sig")
        html = html.replace("%TARGETS_JSON%", json.dumps(server.TARGETS, ensure_ascii=False)).replace("%DEFAULT_MODEL%", server.DEFAULT_MODEL)
        script = "\n".join(re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html))
        result = subprocess.run([shutil.which("node"), "--check"], input=script, capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def js(self, functions, expression, setup=""):
        html = (ROOT / "kanpan-tool/index.html").read_text(encoding="utf-8-sig")
        snippets = []
        for name in functions:
            match = re.search(r"    (?:async )?function " + re.escape(name) + r"\b[\s\S]*?\n    \}", html)
            self.assertIsNotNone(match, name)
            snippets.append(match.group(0))
        if "refreshState" in functions:
            # 原断言保持不变，为页面新增的历史和请求版本状态补齐隔离依赖。
            setup += '''
let stateRequestVersion=0, workspaceBusy=false, currentScene="open";
let lastAnalysisContext=null, lastAnalysisModel="", failedTargets=[];
let stateLoading=false, historyRequestVersion=0, lastAnalysisPreset="default";
const renderHistory=()=>{}, renderAnalysisMeta=()=>{}, updateWorkspace=()=>{};
const originalGetElement=document.getElementById;
document.getElementById=id=>id==="archiveDates"?{replaceChildren:()=>{}}:originalGetElement(id);
'''
        code = setup + "\n" + "\n".join(snippets) + "\nPromise.resolve((async () => {" + expression + "})()).then(value => console.log(JSON.stringify(value))).catch(error => {console.error(error); process.exitCode = 1;});"
        result = subprocess.run([shutil.which("node")], input=code, capture_output=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def model_js(self, expression, extra_setup="", functions=()):
        setup = r'''
const DEFAULT_MODEL = "test-default";
const nodes = Object.fromEntries(["kimiKey", "kimiModel", "kimiModelOptions", "kimiKeyHint", "modelConfigFeedback", "fetchModelsButton", "testModelButton", "saveSettingsButton", "clearKeyButton"].map(id => [id, {
  value: "", disabled: false, children: [],
  replaceChildren() { this.children = []; },
  appendChild(child) { this.children.push(child); }
}]));
nodes.kimiKey.value = "****";
nodes.kimiModel.value = "saved-model";
const document = {getElementById: id => nodes[id], createElement: tag => ({tagName: tag})};
let settingsState = {kimiModel: "saved-model", hasKimiKey: true, kimiKeyMask: "****"};
let availableKimiModels = [];
let modelConfigurationBusy = false;
let settingsMutationVersion = 0;
const normalizeErrorMessage = error => error.message;
const setStatus = () => {};
'''
        helpers = ["currentDisplayedKimiKey", "currentKimiKeyValue", "selectedKimiModel", "renderModelOptions", "setModelFeedback", "setModelConfigurationBusy", "buildSettingsPayload", "applySettings", "saveCurrentSettings"]
        return self.js([*helpers, *functions], expression, setup + extra_setup)

    def test_deep_saved_new_key_is_cleared_for_followup_calls(self):
        setup = 'nodes.kimiKey.value="test-new-key"; const requests=[]; const postJson=async(path,payload)=>{requests.push({path,payload}); return {ok:true,kimiModel:payload.kimi_model,hasKimiKey:true,kimiKeyMask:"new-mask"};};'
        value = self.model_js('await saveSettings(); return {requests:requests.length,key:nodes.kimiKey.value,raw:currentKimiKeyValue()};', setup, ["saveSettings"])
        self.assertEqual(value, {"requests": 1, "key": "", "raw": ""})

    def test_deep_stale_settings_response_cannot_undo_new_save(self):
        setup = 'let complete; const fetch=()=>new Promise(resolve=>{complete=resolve;}); const postJson=async(path,payload)=>({ok:true,kimiModel:payload.kimi_model,hasKimiKey:true,kimiKeyMask:"****"});'
        expression = 'const pending=loadSettings(); nodes.kimiModel.value="chosen-model"; await saveSettings(); complete({ok:true,json:async()=>({kimiModel:"old-model",hasKimiKey:true,kimiKeyMask:"****"})}); await pending; return {selected:nodes.kimiModel.value,saved:settingsState.kimiModel};'
        value = self.model_js(expression, setup, ["loadSettings", "saveSettings"])
        self.assertEqual(value, {"selected": "chosen-model", "saved": "chosen-model"})

    def test_deep_stale_state_response_cannot_undo_new_save(self):
        setup = 'nodes.tradeDate={value:"2026-10-20"}; let state={currentDate:"2026-10-20"}; let activeJobId=""; const updateMetrics=()=>{}; let complete; const fetch=()=>new Promise(resolve=>{complete=resolve;}); const postJson=async(path,payload)=>({ok:true,kimiModel:payload.kimi_model,hasKimiKey:true,kimiKeyMask:"****"});'
        expression = 'const pending=refreshState(); nodes.kimiModel.value="chosen-model"; await saveSettings(); complete({ok:true,json:async()=>({currentDate:"2026-10-20",availableTargets:[],settings:{kimiModel:"old-model",hasKimiKey:true,kimiKeyMask:"****"}})}); await pending; return {selected:nodes.kimiModel.value,saved:settingsState.kimiModel};'
        value = self.model_js(expression, setup, ["refreshState", "saveSettings"])
        self.assertEqual(value, {"selected": "chosen-model", "saved": "chosen-model"})

    def test_deep_invalid_save_acknowledgement_does_not_report_success(self):
        setup = 'nodes.kimiModel.value="chosen-model"; const postJson=async()=>({ok:false,kimiModel:"other-model",hasKimiKey:true,kimiKeyMask:"****"});'
        value = self.model_js('await saveSettings(); return {feedback:nodes.modelConfigFeedback.textContent,model:nodes.kimiModel.value,busy:modelConfigurationBusy,saved:settingsState.kimiModel};', setup, ["saveSettings"])
        self.assertIn("保存失败", value["feedback"])
        self.assertEqual(value["model"], "chosen-model")
        self.assertEqual(value["saved"], "saved-model")
        self.assertFalse(value["busy"])

    def test_deep_save_failure_keeps_unsaved_fields_and_unlocks(self):
        setup = 'nodes.kimiModel.value="chosen-model"; nodes.kimiKey.value="new-test-key"; const postJson=async()=>{throw new Error("disk full");};'
        value = self.model_js('await saveSettings(); return {feedback:nodes.modelConfigFeedback.textContent,model:nodes.kimiModel.value,key:nodes.kimiKey.value,disabled:nodes.saveSettingsButton.disabled,busy:modelConfigurationBusy};', setup, ["saveSettings"])
        self.assertIn("disk full", value["feedback"])
        self.assertEqual(value["model"], "chosen-model")
        self.assertEqual(value["key"], "new-test-key")
        self.assertFalse(value["disabled"])
        self.assertFalse(value["busy"])

    def test_deep_invalid_model_response_does_not_corrupt_existing_options(self):
        for models in [None, [], "not-a-list", [123]]:
            with self.subTest(models=models):
                setup = 'availableKimiModels=["saved-model","old-option"]; const postJson=async()=>(' + json.dumps({"models": models}) + ');'
                expression = 'await fetchKimiModels(); let crashed=false; try {applySettings({kimiModel:"saved-model",hasKimiKey:true,kimiKeyMask:"****"});} catch {crashed=true;} return {available:availableKimiModels,crashed,feedback:nodes.modelConfigFeedback.textContent};'
                value = self.model_js(expression, setup, ["fetchKimiModels"])
                self.assertEqual(value["available"], ["saved-model", "old-option"])
                self.assertFalse(value["crashed"])
                self.assertIn("失败", value["feedback"])

    def test_deep_invalid_model_test_response_is_never_success(self):
        cases = [
            {"ok": False, "model": "saved-model", "reply": "OK", "elapsed_ms": 1},
            {"ok": True, "model": "other-model", "reply": "OK", "elapsed_ms": 1},
            {"ok": True, "model": "saved-model", "reply": "", "elapsed_ms": 1},
            {"ok": True, "model": "saved-model", "reply": "OK", "elapsed_ms": -1},
        ]
        for response in cases:
            with self.subTest(response=response):
                setup = 'const postJson=async()=>(' + json.dumps(response) + ');'
                value = self.model_js('await testSelectedModel(); return nodes.modelConfigFeedback.textContent;', setup, ["testSelectedModel"])
                self.assertIn("测试失败", value)
                self.assertNotIn("测试通过", value)

    def test_model_list_keeps_selection_and_does_not_save(self):
        setup = 'const requests=[]; const postJson=async(path,payload)=>{requests.push({path,payload}); return {models:["listed-a","listed-b"]};};'
        value = self.model_js('await fetchKimiModels(); return {requests,selected:nodes.kimiModel.value,options:nodes.kimiModelOptions.children.map(x=>x.value),feedback:nodes.modelConfigFeedback.textContent,busy:modelConfigurationBusy};', setup, ["fetchKimiModels"])
        self.assertEqual(value["requests"], [{"path": "/api/models", "payload": {"api_key": ""}}])
        self.assertEqual(value["selected"], "saved-model")
        self.assertEqual(value["options"], ["", "saved-model", "listed-a", "listed-b"])
        self.assertIn("已获取 2 个模型", value["feedback"])
        self.assertFalse(value["busy"])

    def test_model_test_uses_unsaved_selection_and_input_key(self):
        setup = 'nodes.kimiKey.value="test-transient-key"; nodes.kimiModel.value="unsaved-model"; const requests=[]; const postJson=async(path,payload)=>{requests.push({path,payload}); return {ok:true,model:payload.model,reply:"OK",elapsed_ms:120};};'
        value = self.model_js('await testSelectedModel(); return {requests,feedback:nodes.modelConfigFeedback.textContent,disabled:nodes.testModelButton.disabled,savedModel:settingsState.kimiModel};', setup, ["testSelectedModel"])
        self.assertEqual(value["requests"], [{"path": "/api/model-test", "payload": {"api_key": "test-transient-key", "model": "unsaved-model"}}])
        self.assertIn("测试通过：unsaved-model", value["feedback"])
        self.assertIn("0.12 秒", value["feedback"])
        self.assertFalse(value["disabled"])
        self.assertEqual(value["savedModel"], "saved-model")

    def test_model_test_ignores_duplicate_clicks_while_busy(self):
        setup = 'let complete; let calls=0; const postJson=()=>{calls++; return new Promise(resolve=>{complete=resolve;});};'
        expression = 'const first=testSelectedModel(); const locked=nodes.kimiModel.disabled; await testSelectedModel(); complete({ok:true,model:"saved-model",reply:"OK",elapsed_ms:1}); await first; return {calls,locked,busy:modelConfigurationBusy,disabled:nodes.kimiModel.disabled};'
        value = self.model_js(expression, setup, ["testSelectedModel"])
        self.assertEqual(value, {"calls": 1, "locked": True, "busy": False, "disabled": False})

    def test_model_operation_failure_unlocks_controls_and_keeps_selection(self):
        setup = 'const postJson=async()=>{throw new Error("HTTP 401");};'
        value = self.model_js('await fetchKimiModels(); return {feedback:nodes.modelConfigFeedback.textContent,style:nodes.modelConfigFeedback.className,selected:nodes.kimiModel.value,disabled:nodes.fetchModelsButton.disabled,busy:modelConfigurationBusy};', setup, ["fetchKimiModels"])
        self.assertIn("HTTP 401", value["feedback"])
        self.assertIn("error", value["style"])
        self.assertEqual(value["selected"], "saved-model")
        self.assertFalse(value["disabled"])
        self.assertFalse(value["busy"])

    def test_saving_selected_model_preserves_blank_saved_key(self):
        setup = 'nodes.kimiKey.value=""; nodes.kimiModel.value="chosen-model"; const requests=[]; const postJson=async(path,payload)=>{requests.push({path,payload}); return {ok:true,kimiModel:payload.kimi_model,hasKimiKey:true,kimiKeyMask:"****"};};'
        value = self.model_js('await saveSettings(); return {requests,selected:nodes.kimiModel.value,saved:settingsState.kimiModel,feedback:nodes.modelConfigFeedback.textContent};', setup, ["saveSettings"])
        self.assertEqual(value["requests"], [{"path": "/api/settings", "payload": {"kimi_key": "", "kimi_model": "chosen-model", "keep_existing_key": True}}])
        self.assertEqual(value["selected"], "chosen-model")
        self.assertEqual(value["saved"], "chosen-model")
        self.assertIn("已保存选定模型", value["feedback"])

    def test_saved_model_restores_before_model_list_has_loaded(self):
        value = self.model_js('applySettings({kimiModel:"restored-model",hasKimiKey:true,kimiKeyMask:"****"}); return {selected:nodes.kimiModel.value,options:nodes.kimiModelOptions.children.map(x=>x.value)};')
        self.assertEqual(value, {"selected": "restored-model", "options": ["", "restored-model"]})

    def test_refresh_settings_does_not_discard_unsaved_model_selection(self):
        value = self.model_js('nodes.kimiModel.value="unsaved-model"; applySettings({kimiModel:"saved-model",hasKimiKey:true,kimiKeyMask:"****"}); return nodes.kimiModel.value;')
        self.assertEqual(value, "unsaved-model")

    def test_model_ids_are_rendered_as_text_not_html(self):
        value = self.model_js('availableKimiModels=[\'model"><img src=x onerror=alert(1)>\']; renderModelOptions(); return nodes.kimiModelOptions.children[2];')
        self.assertEqual(value["value"], 'model"><img src=x onerror=alert(1)>')
        self.assertEqual(value["textContent"], value["value"])
        self.assertNotIn("innerHTML", value)

    def test_clearing_key_discards_model_list_for_previous_key(self):
        value = self.model_js('availableKimiModels=["other-key-model"]; clearKeyInput(); return {available:availableKimiModels,selected:nodes.kimiModel.value,key:nodes.kimiKey.value};', functions=["resetModelsForKeyInput", "clearKeyInput"])
        self.assertEqual(value, {"available": [], "selected": "saved-model", "key": ""})

    def test_long_error_does_not_expand_metric_status(self):
        value = self.js(["setStatus"], 'setStatus("竞价封单 抓取失败，退出码 1"); return {metric:nodes.metricStatus.textContent, detail:nodes.statusText.textContent};', 'const nodes={statusText:{},metricStatus:{},metricStatusLegacy:{}}; const document={getElementById:id=>nodes[id]};')
        self.assertLessEqual(len(value["metric"]), 4)
        self.assertIn("退出码 1", value["detail"])

    def test_failed_fetch_is_not_shown_as_ready(self):
        setup = 'const root={}; const document={getElementById:()=>root}; const targetLabel=()=>"竞价"; const targetFullLabel=()=>"竞价封单";'
        value = self.js(["escapeHtml", "renderTargetData"], 'renderTargetData([{target:"jingjia",exists:"true",fetch_status:"failed"}]); return root.innerHTML;', setup)
        self.assertIn("本次抓取失败", value)
        self.assertNotIn("已就绪", value)

    def test_refresh_keeps_explicit_date_input(self):
        setup = 'let settingsMutationVersion=0; let state={currentDate:"2026-10-19"}; let activeJobId=""; let requested=""; const input={value:"2026-10-19"}; const document={getElementById:()=>input}; const setStatus=()=>{}; const updateMetrics=()=>{}; const fetch=async url=>{requested=url; return {ok:true,json:async()=>({currentDate:"2026-10-19",availableTargets:[]})}};'
        value = self.js(["refreshState"], 'await refreshState(); return requested;', setup)
        self.assertEqual(value, "/api/state?date=2026-10-19")

    def test_changing_date_clears_previous_analysis(self):
        setup = 'let settingsMutationVersion=0; let state={currentDate:"2026-07-24"}; let activeJobId=""; let prompt="old"; let analysis="old"; let lastAnalyzeTargets=["jingjia"]; let lastPromptPreset="close"; const selectedTargets=new Set(["jingjia"]); const input={value:"2026-10-19"}; const document={getElementById:()=>input}; const setStatus=()=>{}; const updateMetrics=()=>{}; const setPromptContent=value=>{prompt=value}; const setAnalysisContent=value=>{analysis=value}; const orderedTargetList=value=>value; const renderTargetData=()=>{}; const renderProgress=()=>{}; const fetch=async()=>({ok:true,json:async()=>({currentDate:"2026-10-19",availableTargets:[]})});'
        value = self.js(["refreshState"], 'await refreshState(); return {prompt,analysis,targets:lastAnalyzeTargets};', setup)
        self.assertEqual(value, {"prompt": "", "analysis": "", "targets": []})

    def test_markdown_rendering_and_literal_code(self):
        value = self.js(["escapeHtml", "splitTableRow", "isTableDivider", "parseMarkdownBlocks", "renderInlineMarkdown", "renderMarkdown"], 'return renderMarkdown("# 标题\\n\\n正文 `a_b` 与 **重点**\\n\\n- 第一项\\n- 第二项");')
        self.assertIn("<h1>标题</h1>", value)
        self.assertIn("<code>a_b</code>", value)
        self.assertIn("<strong>重点</strong>", value)
        self.assertIn("<ul>", value)

    def test_markdown_table_preserves_escaped_pipe(self):
        value = self.js(["splitTableRow"], r'return splitTableRow("| A\\|B | C |");')
        self.assertEqual(value, ["A|B", "C"])


if __name__ == "__main__":
    unittest.main()
