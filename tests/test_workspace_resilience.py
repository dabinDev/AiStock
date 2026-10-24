from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from test_kanpan_regressions import WorkspaceTest, server


class WorkspaceResilienceTests(WorkspaceTest):
    def test_invalid_analysis_fields_are_rejected_before_creating_job(self):
        self.put("jingjia")
        with patch.object(server, "start_analyze_job", return_value="should-not-start") as start, self.api() as request:
            for field, value in [("model", 123), ("model", []), ("model", {}), ("preset", []), ("prompt_override", 123)]:
                with self.subTest(field=field, value=value):
                    status, body = request("/api/analyze", {"date": self.today, "targets": ["jingjia"], "api_key": "fake-test-key", field: value})
                    self.assertEqual(status, 400)
                    self.assertIn("error", body)
        start.assert_not_called()

    def test_unreadable_module_does_not_break_other_data_and_blocks_analysis(self):
        self.put("jingjia").write_bytes(b"\xff\xfe\x00\x81")
        self.put("jjyd")
        with self.api() as request:
            status, data = request(f"/api/state?date={self.today}")
        self.assertEqual(status, 200)
        items = {item["target"]: item for item in data["targetData"]}
        self.assertFalse(items["jingjia"]["usable"])
        self.assertEqual(items["jingjia"]["quality"], "read_error")
        self.assertTrue(items["jjyd"]["usable"])
        with self.assertRaisesRegex(RuntimeError, "读取"):
            server.build_prompt_payload(self.today, ["jingjia"])

    def test_explicit_malformed_time_is_not_treated_as_missing_legacy_time(self):
        for captured in ["yesterday", "2026-10-20", "2026-10-20 10:05:00 invalid", ""]:
            with self.subTest(captured=captured):
                body = f"# 数据\n> 数据日期：2026-10-20\n> 生成时间：{captured}\n\n有效正文"
                item = server.data_quality(body, "2026-10-20", "jingjia")
                self.assertFalse(item["usable"])
                self.assertEqual(item["quality"], "invalid_time")

    def test_corrupt_history_metadata_is_skipped_without_hiding_good_records(self):
        meta = {"preset": "open", "model": "fake-model", "targets": ["jingjia"], "generated_at": "2026-10-20 10:00:00", "context": {"title_time": "10-20 09:25"}}
        good = server.save_analysis(self.today, "jingjia", "好记录", meta)
        bad = server.save_analysis(self.today, "jingjia", "坏记录", meta)
        path = self.data / self.today / bad.replace(".md", ".json")
        original = json.loads(path.read_text(encoding="utf-8"))
        for field, value in [("targets", "jingjia"), ("targets", ["invalid"]), ("context", "bad"), ("analysis", ["bad"]), ("generated_at", 123), ("date", "2020-01-01")]:
            with self.subTest(field=field):
                path.write_text(json.dumps({**original, field: value}), encoding="utf-8")
                self.assertEqual([item["id"] for item in server.analysis_history(self.today)], [good])
                with self.assertRaises(ValueError):
                    server.read_analysis(self.today, bad)

    def test_freshness_boundaries_and_historical_data(self):
        for clock, capture, usable in [("09:29:00", "09:00:00", True), ("10:15:00", "10:00:00", True), ("10:15:01", "10:00:00", False), ("12:30:00", "11:30:00", True), ("13:00:00", "11:30:00", False), ("16:00:00", "15:00:00", True)]:
            with self.subTest(clock=clock), patch.object(server, "now_text", return_value=f"2026-10-20 {clock}"):
                content = f"# 数据\n> 数据日期：2026-10-20\n> 生成时间：2026-10-20 {capture}\n\n有效资料"
                self.assertEqual(server.data_quality(content, "2026-10-20", "yidong")["usable"], usable)
        with patch.object(server, "now_text", return_value="2026-10-21 10:30:00"):
            self.assertTrue(server.data_quality(content, "2026-10-20", "yidong")["usable"])

    def test_missing_or_unknown_job_returns_json_error(self):
        with self.api() as request:
            self.assertEqual(request("/api/job")[0], 400)
            self.assertEqual(request("/api/job?id=missing-job")[0], 404)

    def test_invalid_model_in_direct_worker_ends_in_failure_without_upstream(self):
        with patch.object(server, "call_kimi") as call:
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "fake-key", 123))
        self.assertEqual(result["status"], "failed")
        call.assert_not_called()

    def test_fetch_zero_exit_with_unreadable_output_is_failure(self):
        def process(command, **kwargs):
            if "--targets" in command:
                self.put(command[command.index("--targets") + 1]).write_bytes(b"\xff\x81")
            return SimpleNamespace(stdout=io.StringIO("模拟采集\n"), wait=lambda: 0)

        with patch.object(server.subprocess, "Popen", side_effect=process):
            result = self.wait_job(server.start_fetch_job(self.today, ["jingjia"]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["target_statuses"]["jingjia"], "failed")
        self.assertEqual(result["result"]["fetched_count"], 0)
        self.assertIn("读取", result["error"])
