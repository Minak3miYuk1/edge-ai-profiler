import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from edgeprof.collectors import ResourceCollector

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_resource_counters_are_deltas_and_missing_is_not_zero(self):
        c = ResourceCollector(123)
        self.assertEqual(c.snapshot({"voluntary": 10, "involuntary": 2}), {})
        c.set_enabled(True)
        self.assertEqual(c.snapshot({"voluntary": 14, "involuntary": 3}),
                         {"voluntary_switches": 4, "involuntary_switches": 1})
        c.set_enabled(False)
        self.assertEqual(c.snapshot({"voluntary": 20, "involuntary": 3}), {})
        c.set_enabled(True)
        self.assertEqual(c.snapshot({"voluntary": 22, "involuntary": 3})["voluntary_switches"], 2)

    def test_end_to_end_slowdown_triggers_and_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "main.py", "--out", temp,
                "--duration", "4.2", "--window", "0.2", "--calibration", "0.4",
                "--slow-start", "0.6", "--slow-duration", "1.4", "--min-detailed", "0.4",
                "--slow-delay-ms", "15"], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            folder = Path(temp)
            summary = json.loads((folder / "summary.json").read_text())
            transitions = summary["transitions"]
            self.assertTrue(any(e["to"] == "detailed" for e in transitions), transitions)
            self.assertTrue(any(e["to"] == "basic" for e in transitions), transitions)
            self.assertEqual(summary["backend"], "resource")
            with (folder / "windows.csv").open() as f:
                rows = list(csv.DictReader(f))
            self.assertGreater(len(rows), 10)
            self.assertTrue(all(r["switch_out"] == "" for r in rows))
            self.assertTrue(any(r["mode"] == "detailed" and r["voluntary_switches"] != "" for r in rows))
            self.assertTrue(all(r["voluntary_switches"] == "" for r in rows if r["mode"] == "basic"))
            self.assertIn("NOT", result.stdout)
            self.assertIn("Portable resource counters", (folder / "report.html").read_text())

    def test_off_mode_never_exports_detailed_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "main.py", "--out", temp,
                "--mode", "off", "--duration", "0.5", "--window", "0.2", "--calibration", "0.2"],
                cwd=ROOT, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((Path(temp) / "summary.json").read_text())
            self.assertEqual(summary["backend"], "none")
            self.assertEqual(summary["transitions"], [])
            self.assertEqual(summary["detailed_duty_percent"], 0)

    def test_bcc_has_no_silent_fallback_on_macos(self):
        if sys.platform != "darwin":
            self.skipTest("macOS-specific platform rejection")
        result = subprocess.run([sys.executable, "main.py", "--backend", "bcc"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires Linux", result.stderr)


if __name__ == "__main__":
    unittest.main()
