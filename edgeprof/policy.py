"""Latency hysteresis; independent of the workload and collector."""
from dataclasses import dataclass
import math


@dataclass
class Policy:
    baseline_ms: float
    enter_ratio: float = 1.20
    exit_ratio: float = 1.10
    trigger_windows: int = 2
    recovery_windows: int = 3
    min_detailed_s: float = 5.0
    mode: str = "basic"
    high: int = 0
    low: int = 0
    entered_at: float = 0.0

    def __post_init__(self):
        if not math.isfinite(self.baseline_ms) or self.baseline_ms <= 0:
            raise ValueError("baseline must be finite and positive")
        if not (1 <= self.exit_ratio < self.enter_ratio):
            raise ValueError("require 1 <= exit_ratio < enter_ratio")
        if self.trigger_windows < 1 or self.recovery_windows < 1:
            raise ValueError("window counts must be positive")
        if self.min_detailed_s < 0:
            raise ValueError("minimum detailed duration cannot be negative")

    def observe(self, median_ms, now, valid=True):
        """Return a transition reason, or None. New mode applies to the NEXT window."""
        if not valid or not math.isfinite(median_ms) or median_ms <= 0:
            self.high = self.low = 0
            return None
        if self.mode == "basic":
            self.high = self.high + 1 if median_ms > self.baseline_ms * self.enter_ratio else 0
            if self.high >= self.trigger_windows:
                self.mode = "detailed"
                self.entered_at = now
                self.high = self.low = 0
                return "latency_above_threshold"
        else:
            self.low = self.low + 1 if median_ms < self.baseline_ms * self.exit_ratio else 0
            if self.low >= self.recovery_windows and now - self.entered_at >= self.min_detailed_s:
                self.mode = "basic"
                self.high = self.low = 0
                return "latency_recovered"
        return None
