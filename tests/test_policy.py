import math
import unittest
from edgeprof.policy import Policy


class PolicyTests(unittest.TestCase):
    def test_spikes_must_be_consecutive_and_hysteresis_keeps_mode(self):
        p = Policy(10)
        self.assertIsNone(p.observe(13, 1))
        self.assertIsNone(p.observe(10, 2))
        self.assertIsNone(p.observe(13, 3))
        self.assertEqual(p.mode, "basic")
        self.assertEqual(p.observe(13, 4), "latency_above_threshold")
        p.observe(11.5, 10)
        self.assertEqual(p.mode, "detailed")

    def test_recovery_requires_both_duration_and_consecutive_windows(self):
        p = Policy(10, min_detailed_s=5)
        p.observe(13, 1)
        p.observe(13, 2)
        for t in (3, 4, 5, 6):
            self.assertIsNone(p.observe(10, t))
        self.assertEqual(p.mode, "detailed")
        self.assertEqual(p.observe(10, 7), "latency_recovered")
        self.assertEqual(p.mode, "basic")

    def test_invalid_windows_break_the_trigger_sequence(self):
        p = Policy(10)
        p.observe(13, 1)
        p.observe(30, 2, valid=False)
        p.observe(13, 3)
        self.assertEqual(p.mode, "basic")
        p.observe(math.nan, 4)
        p.observe(13, 5)
        self.assertEqual(p.mode, "basic")

    def test_strict_thresholds_and_bad_configuration(self):
        p = Policy(10)
        for t in range(5):
            p.observe(12, t)
        self.assertEqual(p.mode, "basic")
        for baseline in (0, -1, math.inf, math.nan):
            with self.assertRaises(ValueError):
                Policy(baseline)


if __name__ == "__main__":
    unittest.main()
