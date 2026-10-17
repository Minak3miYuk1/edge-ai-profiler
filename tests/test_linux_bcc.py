"""Opt in on a real Linux host: EDGEPROF_TEST_BCC=1 python3 -m unittest discover -s tests -v"""
import os
import subprocess
import sys
import time
import unittest
from edgeprof.collectors import BCCCollector


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("EDGEPROF_TEST_BCC") == "1",
                     "requires Linux, BCC privileges and explicit EDGEPROF_TEST_BCC=1")
class LinuxBCCTests(unittest.TestCase):
    def test_real_target_switches_and_disabled_gate(self):
        target = subprocess.Popen([sys.executable, "-c", "import time;\nfor _ in range(30000): time.sleep(.001)"])
        collector = None
        try:
            collector = BCCCollector(target.pid)
            collector.snapshot({})
            collector.set_enabled(True)
            time.sleep(0.3)
            counts = collector.snapshot({})
            self.assertGreater(counts["switch_out"], 0)
            self.assertGreater(counts["switch_in"], 0)
            collector.set_enabled(False)
            collector.snapshot({})  # Drain any event concurrent with the mode change.
            baseline = collector.last
            time.sleep(0.2)
            self.assertEqual(collector.snapshot({}), {})
            self.assertEqual(collector.last, baseline)
        finally:
            if collector is not None:
                collector.close()
            target.terminate()
            target.wait(timeout=5)
