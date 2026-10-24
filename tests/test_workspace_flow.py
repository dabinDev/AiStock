from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import Mock, patch

from test_kanpan_regressions import ROOT, WorkspaceTest, bundle, server


class DataQualityTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        patch.object(server, "now_text", return_value="2026-10-20 16:00:00").start()

    def put_snapshot(self, target, day="2026-10-20", captured="2026-10-20 10:05:00", source_day=None):
        return self.put(target, day, f"# 数据\n> 数据日期：{source_day or day}\n> 生成时间：{captured}\n> 个股条数：1\n\n| 名称 | 代码 |\n| --- | --- |\n| 示例 | 000001 |\n")

    def test_state_returns_existing_data_metadata_on_first_load(self):
        self.put_snapshot("jingjia")
        with self.api() as request:
            status, data = request("/api/state?date=2026-10-20")
        self.assertEqual(status, 200)
        item = next(x for x in data["targetData"] if x["target"] == "jingjia")
        self.assertEqual(item["data_date"], "2026-10-20")
        self.assertEqual(item["captured_at"], "2026-10-20 10:05:00")
        self.assertEqual(item["row_count"], 1)
        self.assertTrue(item["usable"])

    def test_empty_and_header_only_data_are_rejected_before_model_call(self):
        for content in ["", "# 只有标题\n> 生成时间：2026-10-20 10:05:00\n", "| 名称 | 代码 |\n| --- | --- |\n| - | - |\n"]:
            with self.subTest(content=content):
                self.put("jingjia", content=content)
                with self.assertRaisesRegex(RuntimeError, "空|无有效"):
                    server.build_prompt_payload(self.today, ["jingjia"], preset="open")
                with patch.object(server, "call_kimi") as call:
                    result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-key", "test-model"))
                self.assertEqual(result["status"], "failed")
                call.assert_not_called()

    def test_wrong_source_date_is_not_silently_used(self):
        self.put_snapshot("fupan", source_day="2026-10-19")
        with self.assertRaisesRegex(RuntimeError, "日期"):
            server.build_prompt_payload("2026-10-20", ["fupan"], preset="close")

    def test_dynamic_age_uses_session_clock_but_auction_is_fixed(self):
        self.put_snapshot("yidong", captured="2026-10-20 10:00:00")
        self.put_snapshot("jingjia", captured="2026-10-20 09:25:00")
        with patch.object(server, "now_text", return_value="2026-10-20 10:30:00"):
            data = {x["target"]: x for x in server.collect_target_contents("2026-10-20", ["yidong", "jingjia"])}
        self.assertEqual(data["yidong"]["quality"], "stale")
        self.assertFalse(data["yidong"]["usable"])
        self.assertTrue(data["jingjia"]["usable"])

    def test_lunch_and_after_close_do_not_age_fresh_session_data(self):
        for now, captured in [("12:50:00", "11:30:00"), ("20:00:00", "15:00:00")]:
            with self.subTest(now=now):
                self.put_snapshot("yidong", captured=f"2026-10-20 {captured}")
                with patch.object(server, "now_text", return_value=f"2026-10-20 {now}"):
                    item = server.collect_target_contents("2026-10-20", ["yidong"])[0]
                self.assertTrue(item["usable"])

    def test_historical_and_legacy_data_remain_readable_with_honest_time(self):
        self.put_snapshot("yidong", day="2026-10-19", captured="2026-10-19 10:05:00")
        prompt = server.build_prompt_payload("2026-10-19", ["yidong"], preset="intraday")
        self.assertEqual(prompt["context"]["title_time"], "10-19 10:05")
        self.put("jingjia", content="# 旧归档\n\n已有有效正文\n")
        item = server.collect_target_contents(self.today, ["jingjia"])[0]
        self.assertEqual(item["quality"], "unknown")
        self.assertTrue(item["usable"])
        self.assertIn("时间", item["quality_message"])

    def test_failed_refresh_cannot_reuse_old_file_as_successful_input(self):
        self.put("jingjia")
        job_id = server.create_job("fetch", "测试失败", {"date": self.today, "targets": ["jingjia"]})
        server.set_job_target_status(job_id, "jingjia", "failed")
        server.finish_job(job_id, error="源站不可用")
        with self.assertRaisesRegex(RuntimeError, "失败"):
            server.build_prompt_payload(self.today, ["jingjia"])
        self.assertTrue((self.data / self.today / "jingjia.md").exists())

    def test_invalid_capture_time_does_not_break_state_or_pass_validation(self):
        self.put_snapshot("jingjia", captured="2026-10-20 29:00:00")
        with self.api() as request:
            status, data = request("/api/state?date=2026-10-20")
        self.assertEqual(status, 200)
        item = next(x for x in data["targetData"] if x["target"] == "jingjia")
        self.assertFalse(item["usable"])
        self.assertEqual(item["quality"], "invalid_time")

    def test_future_capture_time_cannot_look_fresh(self):
        self.put_snapshot("yidong", captured="2026-10-20 14:00:00")
        with patch.object(server, "now_text", return_value="2026-10-20 10:30:00"):
            item = server.collect_target_contents("2026-10-20", ["yidong"])[0]
        self.assertFalse(item["usable"])
        self.assertEqual(item["quality"], "invalid_time")

    def test_settings_report_environment_key_availability_without_disclosing_it(self):
        with patch.dict(server.os.environ, {"MOONSHOT_API_KEY": "fake-environment-key"}), self.api() as request:
            for url in ["/api/settings", "/api/state"]:
                _, data = request(url)
                settings = data.get("settings", data)
                self.assertTrue(settings["hasEffectiveKimiKey"])
                self.assertNotIn("fake-environment-key", json.dumps(data))


class AnalysisHistoryTests(WorkspaceTest):
    def analyze(self, text="测试正文", preset="open"):
        self.put("jingjia")
        with patch.object(server, "call_kimi", return_value=text):
            return self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "history-test-key", "test-model", preset=preset))

    def test_repeated_analysis_has_independent_reproducible_records(self):
        first = self.analyze("第一次结论")
        second = self.analyze("第二次结论")
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertNotEqual(first["result"]["analysis_file"], second["result"]["analysis_file"])
        with self.api() as request:
            status, listing = request(f"/api/history?date={self.today}")
            self.assertEqual(status, 200)
            self.assertEqual(len(listing["records"]), 2)
            _, detail = request(f"/api/analysis?date={self.today}&id={first['result']['record_id']}")
        self.assertEqual(detail["analysis"], "第一次结论")
        self.assertEqual(detail["model"], "test-model")
        self.assertEqual(detail["preset"], "open")
        self.assertEqual(detail["prompt_sha256"], hashlib.sha256(detail["prompt"].encode()).hexdigest())
        self.assertEqual(detail["inputs"][0]["sha256"], hashlib.sha256(server.sanitize_text((self.data / self.today / "jingjia.md").read_text(encoding="utf-8-sig")).encode()).hexdigest())
        self.assertNotIn("history-test-key", json.dumps(detail))
        self.assertIn("generated_at", detail)
        self.assertIn("context", detail)

    def test_history_reads_legacy_markdown_without_rewriting(self):
        path = self.data / "2026-10-19" / "kimi-analysis-jingjia-jjyd.md"
        path.parent.mkdir(parents=True)
        path.write_text("10-19 09:25\n\n旧版结论", encoding="utf-8-sig")
        original = path.read_bytes()
        with self.api() as request:
            status, listing = request("/api/history?date=2026-10-19")
            self.assertEqual(status, 200)
            _, detail = request("/api/analysis?date=2026-10-19&id=" + listing["records"][0]["id"])
            for value in ["../settings.json", "%2e%2e%2fsettings.json", "not-a-record.json"]:
                bad_status, _ = request("/api/analysis?date=2026-10-19&id=" + value)
                self.assertIn(bad_status, [400, 404])
        self.assertTrue(detail["legacy"])
        self.assertEqual(detail["context"]["title_time"], "10-19 09:25")
        self.assertEqual(original, path.read_bytes())

    def test_record_write_failure_is_not_reported_as_completed(self):
        self.put("jingjia")
        with patch.object(server, "call_kimi", return_value="正文"), patch.object(server, "save_analysis", side_effect=OSError("磁盘不可写")):
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-key", "test-model"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("磁盘不可写", result["error"])

    def test_record_prompt_and_hash_use_same_snapshot_if_file_changes_during_call(self):
        self.put("jingjia", content="# 数据\n\n第一次数据\n")
        def respond(*args, **kwargs):
            self.put("jingjia", content="# 数据\n\n第二次数据\n")
            return "结论"
        with patch.object(server, "call_kimi", side_effect=respond):
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-key", "model", preset="open"))
        with self.api() as request:
            status, detail = request(f"/api/analysis?date={self.today}&id={result['result']['record_id']}")
        self.assertEqual(status, 200)
        self.assertIn("第一次数据", detail["prompt"])
        self.assertNotIn("第二次数据", detail["prompt"])
        self.assertEqual(detail["inputs"][0]["sha256"], hashlib.sha256("# 数据\n\n第一次数据".encode()).hexdigest())

    def test_history_records_actual_compacted_prompt_used_by_successful_request(self):
        self.put("jingjia")
        rejected = Mock(status_code=413, text="context_length_exceeded")
        rejected.raise_for_status.side_effect = server.requests.HTTPError(response=rejected)
        accepted = Mock()
        with patch.object(server, "compact_prompt_for_kimi", return_value="压缩后实际发送的提示词"), \
             patch.object(server.requests, "post", side_effect=[rejected, accepted]) as post, \
             patch.object(server, "read_kimi_response", return_value="正文"):
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-key", "test-model"))
        self.assertEqual(result["status"], "completed")
        detail = server.read_analysis(self.today, result["result"]["record_id"])
        actual = post.call_args.kwargs["json"]["messages"][-1]["content"]
        self.assertEqual(detail["prompt"], actual)
        self.assertEqual(detail["prompt_sha256"], hashlib.sha256(actual.encode()).hexdigest())
        self.assertEqual(result["result"]["prompt"], actual)
        self.assertEqual(detail["generated_at"], detail["context"]["generated_at"])

    def test_incomplete_metadata_write_is_not_listed_as_success(self):
        self.put("jingjia")
        original = server.atomic_write
        def fail_metadata(path, *args, **kwargs):
            if path.suffix == ".json":
                raise OSError("测试元数据保存失败")
            return original(path, *args, **kwargs)
        with patch.object(server, "call_kimi", return_value="正文"), patch.object(server, "atomic_write", side_effect=fail_metadata):
            result = self.wait_job(server.start_analyze_job(self.today, ["jingjia"], "test-key", "test-model"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(server.analysis_history(self.today), [])


class WorkspaceIntegrationTests(WorkspaceTest):
    def test_application_icon_is_served_as_binary_and_bundled(self):
        html = (ROOT / "kanpan-tool/index.html").read_text(encoding="utf-8")
        self.assertIn('href="/assets/app-icon.ico"', html)
        self.assertEqual(bundle.ICON_FILE, ROOT / "kanpan-tool/assets/app-icon.ico")
        with self.api() as request:
            for name, mime in [("app-icon.ico", "image/x-icon"), ("app-icon.png", "image/png")]:
                status, resource = request("/assets/" + name, raw="bytes")
                self.assertEqual(status, 200)
                self.assertEqual(resource["content_type"], mime)
                self.assertEqual(resource["body"], (ROOT / "kanpan-tool/assets" / name).read_bytes())

    def test_stylesheet_is_served_and_included_in_runtime_assets(self):
        with self.api() as request:
            status, resource = request("/assets/workspace.css", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("text/css", resource["content_type"])
        self.assertEqual(resource["text"], (ROOT / "kanpan-tool/workspace.css").read_text(encoding="utf-8"))
        self.assertIn(ROOT / "kanpan-tool", bundle.RUNTIME_DIRS)

    def test_fetch_prompt_analyze_and_history_share_same_context_over_http(self):
        def export_target(command, **kwargs):
            if "--targets" in command:
                self.put(command[command.index("--targets") + 1])
            return SimpleNamespace(stdout=io.StringIO("模拟采集完成\n"), wait=lambda: 0)
        with patch.object(server.subprocess, "Popen", side_effect=export_target), \
             patch.object(server, "call_kimi", return_value="# 验证结论\n\n已检查本次输入。") as model, \
             self.api() as request:
            status, job = request("/api/fetch", {"date": self.today, "targets": ["jingjia", "jjyd"]})
            self.assertEqual(status, 200)
            self.assertEqual(self.wait_job(job["job_id"])["status"], "completed")
            _, state = request(f"/api/state?date={self.today}")
            self.assertEqual(sum(item["usable"] for item in state["targetData"]), 2)
            _, preview = request("/api/prompt", {"date": self.today, "targets": ["jingjia", "jjyd"], "preset": "open"})
            status, job = request("/api/analyze", {"date": self.today, "targets": ["jingjia", "jjyd"], "preset": "open", "api_key": "test-key", "model": "test-model"})
            self.assertEqual(status, 200)
            result = self.wait_job(job["job_id"])
            self.assertEqual(result["status"], "completed")
            _, listing = request(f"/api/history?date={self.today}")
            self.assertEqual(len(listing["records"]), 1)
            _, detail = request(f"/api/analysis?date={self.today}&id={listing['records'][0]['id']}")
        model.assert_called_once()
        self.assertEqual(detail["context"]["title_time"], preview["context"]["title_time"])
        self.assertEqual(detail["preset"], "open")
        self.assertEqual(detail["targets"], ["jingjia", "jjyd"])
        self.assertFalse(server.SETTINGS_FILE.exists())
