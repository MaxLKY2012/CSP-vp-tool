import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.work = self.base / "考生工作区"
        self.work.mkdir()
        self.opened = []
        self.exam = app.Exam(self.base / "data", opener=self.opened.append)
        self.deadline = time.time() + 60
        self.settings = {"deadline": datetime.fromtimestamp(self.deadline, app.CHINA).isoformat(),
                         "problems": [{"title": t, "slug": s} for t, s in zip(["飞机", "收银员", "沙丘", "曼哈顿"], ["airplane", "cashier", "dune", "manhattan"])]}
        self.exam.configure(self.settings)
        self.candidate = "FJ-00055"

    def write(self, name="airplane", code=b"#include <iostream>\nint main(){return 0;}\n", filename=None):
        path = self.work / self.candidate / name / (filename or name + ".cpp")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(code)
        return path

    def start(self):
        self.exam.start({"candidate": self.candidate, "workspace": str(self.work)})

    def collect(self):
        self.assertTrue(self.exam.collect_due(self.deadline + 0.1))
        return self.exam.state["result"]

    def test_requires_settings_before_start(self):
        other = app.Exam(self.base / "empty")
        with self.assertRaisesRegex(ValueError, "Settings"):
            other.start({"candidate": self.candidate, "workspace": str(self.work)})

    def test_settings_validation(self):
        for invalid in [[], self.settings["problems"][:3], [self.settings["problems"][0]] * 4]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.exam.configure({**self.settings, "problems": invalid})
        for slug in ["../escape", "Airplane", "con", "a/b", "a\\b"]:
            with self.subTest(slug=slug), self.assertRaises(ValueError):
                app.validate_settings({**self.settings, "problems": [{"title": "测试", "slug": slug}] + self.settings["problems"][1:]})
        with self.assertRaises(ValueError):
            app.validate_settings({**self.settings, "deadline": "2000-01-01T00:00:00+08:00"})

    def test_candidate_case_and_separator(self):
        for candidate in ["fj-00055", "FJ_00055", "FJ－00055", "../test", "FJ-../test"]:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.exam.start({"candidate": candidate, "workspace": str(self.work)})

    def test_settings_locked_and_no_automatic_directory_creation(self):
        self.start()
        self.assertFalse((self.work / self.candidate).exists())
        with self.assertRaises(ValueError):
            self.exam.configure(self.settings)
        with self.assertRaises(ValueError):
            self.exam.reset()

    def test_deadline_four_files_bytes_report_and_immutable_snapshot(self):
        for problem in self.settings["problems"]:
            self.write(problem["slug"])
        self.write(filename="airplane.exe", code=b"not source")
        self.start()
        self.assertFalse(self.exam.collect_due(self.deadline - 1))
        result = self.collect()
        self.assertEqual([p["status"] for p in result["problems"]], ["collected"] * 4)
        folder = Path(self.exam.state["report"]).parent
        collected = list((folder / "sources").rglob("*.*"))
        self.assertEqual(len(collected), 4)
        raw = self.write().read_bytes()
        self.assertEqual(result["problems"][0]["bytes"], len(raw))
        self.write(code=b"changed after collection")
        self.assertFalse(self.exam.collect_due(self.deadline + 50))
        self.assertEqual((folder / "sources" / self.candidate / "airplane" / "airplane.cpp").read_bytes(), raw)
        report = Path(self.exam.state["report"]).read_text(encoding="utf-8")
        self.assertEqual(report.count('class="report-card"'), 4)
        self.assertIn("&lt;iostream&gt;", report)
        self.assertEqual(len(self.opened), 1)

    def test_wrong_layout_and_case_not_collected(self):
        self.write(filename="Airplane.cpp")
        self.write("Cashier", filename="cashier.cpp")
        (self.work / self.candidate / "dune.cpp").write_text("root code")
        self.write("manhattan", filename="manhattan.cpp.txt")
        self.start()
        self.assertTrue(all(p["status"] == "missing" for p in self.collect()["problems"]))

    def test_wrong_candidate_directory_not_collected(self):
        self.write()
        (self.work / self.candidate).rename(self.work / "fj-00055")
        self.start()
        self.assertEqual(self.collect()["problems"][0]["status"], "missing")

    def test_empty_gbk_html_escaping_and_original_bytes(self):
        self.write(code='// 中文 </code><script>alert(1)</script>\r\n'.encode("gbk"))
        self.write("cashier", code=b"")
        self.start()
        result = self.collect()
        self.assertEqual(result["problems"][0]["encoding"], "gb18030")
        self.assertEqual(result["problems"][1]["bytes"], 0)
        report = Path(self.exam.state["report"]).read_text(encoding="utf-8")
        self.assertIn("中文", report)
        self.assertNotIn("<script>alert(1)</script>", report)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)

    def test_windows_crlf_not_doubled_in_html(self):
        raw = b"int main() {\r\n    return 0;\r\n}\r\n"
        self.write(code=raw)
        self.start()
        self.collect()
        report = Path(self.exam.state["report"]).read_bytes()
        self.assertIn(raw, report)
        self.assertNotIn(b"\r\r\n", report)
        self.assertEqual(self.exam.state["result"]["problems"][0]["encoding"], "utf-8")

    def test_post_deadline_edits_rejected_and_late_recovery_disclosed(self):
        source = self.write()
        os.utime(source, (self.deadline + 1, self.deadline + 1))
        self.start()
        resumed = app.Exam(self.exam.data_dir, opener=self.opened.append)
        resumed.collect_due(self.deadline + 20)
        self.assertTrue(resumed.state["result"]["late"])
        self.assertEqual(resumed.state["result"]["problems"][0]["status"], "missing")
        self.assertIn("截止时间之后", resumed.state["result"]["problems"][0]["message"])

    def test_oversize_file_rejected(self):
        self.write(code=b"12345")
        with patch.object(app, "MAX_SOURCE", 4):
            self.start()
            self.assertEqual(self.collect()["problems"][0]["status"], "missing")

    def test_read_during_edit_rejected(self):
        source = self.write()
        real_stat = Path.stat
        calls = 0
        def changed_stat(path, *args, **kwargs):
            nonlocal calls
            result = real_stat(path, *args, **kwargs)
            if path == source and not kwargs.get("follow_symlinks") is False:
                calls += 1
                if calls == 2:
                    source.write_bytes(b"new code")
                    result = real_stat(path, *args, **kwargs)
            return result
        with patch.object(Path, "stat", changed_stat):
            result = app.read_problem(self.work, self.candidate, self.settings["problems"][0])
        self.assertEqual(result["status"], "missing")

    def test_junction_is_rejected(self):
        if os.name != "nt":
            self.skipTest("Windows junction check")
        import subprocess
        target = self.base / "outside"
        target.mkdir()
        (target / "airplane.cpp").write_text("outside")
        parent = self.work / self.candidate
        parent.mkdir()
        junction = parent / "airplane"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], check=True, capture_output=True)
        try:
            result = app.read_problem(self.work, self.candidate, self.settings["problems"][0])
            self.assertEqual(result["status"], "missing")
            self.assertIn("链接", result["message"])
        finally:
            junction.rmdir()

    def test_report_export_failure_reuses_frozen_bytes(self):
        original = self.write().read_bytes()
        self.start()
        with patch.object(app, "report_html", side_effect=OSError("disk failure")):
            self.assertFalse(self.exam.collect_due(self.deadline + 0.1))
        self.assertEqual(self.exam.state["phase"], "error")
        self.write(code=b"late replacement")
        with patch.object(app.time, "time", return_value=self.deadline + 1):
            self.exam.retry_export()
        self.assertEqual(self.exam.state["phase"], "collected")
        folder = Path(self.exam.state["report"]).parent
        self.assertEqual((folder / "sources" / self.candidate / "airplane" / "airplane.cpp").read_bytes(), original)

    def test_interrupted_capture_not_silently_retried(self):
        self.start()
        self.exam.state["phase"] = "collecting"
        self.exam.save()
        self.assertFalse(self.exam.collect_due(self.deadline + 1))
        self.assertEqual(self.exam.state["phase"], "error")
        with self.assertRaises(ValueError):
            self.exam.retry_export()

    def test_new_exam_preserves_reports(self):
        self.start()
        self.collect()
        report = Path(self.exam.state["report"])
        self.exam.reset()
        self.assertTrue(report.exists())
        self.assertEqual(self.exam.state["phase"], "setup")

    def test_http_token_origin_and_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(self.exam, "test-token"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with urlopen(url) as response:
            self.assertIn(b"test-token", response.read())
        for headers in [{}, {"X-CSRF-Token": "test-token", "Origin": "https://untrusted.example"}]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url + "/api/start", data=b"{}", headers=headers))
            self.assertEqual(error.exception.code, 403)
        request = Request(url + "/api/start", data=json.dumps({"candidate": self.candidate, "workspace": str(self.work)}).encode(), headers={"X-CSRF-Token": "test-token", "Origin": url})
        with urlopen(request) as response:
            self.assertEqual(json.load(response)["phase"], "running")

    def test_directory_picker_lists_only_folders_and_handles_bad_paths(self):
        self.write()
        (self.work / "not-a-folder.cpp").write_text("test")
        result = app.list_folders(str(self.work))
        self.assertEqual(result["path"], str(self.work.resolve()))
        self.assertEqual([p["name"] for p in result["folders"]], [self.candidate])
        self.assertEqual(result["parent"], str(self.base))
        with self.assertRaises(ValueError):
            app.list_folders(str(self.work / "missing"))


if __name__ == "__main__":
    unittest.main()
