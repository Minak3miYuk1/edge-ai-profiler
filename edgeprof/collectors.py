"""No fake BPF values: explicit resource, bcc, and none backends."""
import ctypes as ct
from pathlib import Path
import sys


def thread_ids(pid):
    return {int(p.name) for p in Path("/proc/{}/task".format(pid)).iterdir()}


class Collector:
    name = "none"

    def __init__(self, pid):
        self.enabled = False

    def set_enabled(self, enabled):
        self.enabled = enabled

    def snapshot(self, usage):
        return {}

    def close(self):
        pass


class ResourceCollector(Collector):
    """Portable, real getrusage counters. Does NOT emulate eBPF."""
    name = "resource"

    def __init__(self, pid):
        super().__init__(pid)
        self.last = None

    def snapshot(self, usage):
        current = (usage["voluntary"], usage["involuntary"])
        last, self.last = self.last, current
        if not self.enabled or last is None:
            return {}
        return {"voluntary_switches": max(0, current[0] - last[0]),
                "involuntary_switches": max(0, current[1] - last[1])}


class BCCCollector(Collector):
    name = "bcc"

    def __init__(self, pid):
        super().__init__(pid)
        if sys.platform != "linux":
            raise RuntimeError("BCC requires Linux; choose --backend resource on macOS")
        try:
            from bcc import BPF
        except ImportError as exc:
            raise RuntimeError("BCC Python binding not found; see README Linux setup") from exc
        self.pid = pid
        self.bpf = None
        self.last = (0, 0)
        try:
            source = Path(__file__).resolve().parent.parent / "bpf" / "sched.c"
            self.bpf = BPF(text=source.read_text())
            self.refresh_threads()
            self.set_enabled(False)
        except Exception as exc:
            self.close()
            raise RuntimeError("BCC probe setup failed: {}".format(exc)) from exc

    def refresh_threads(self):
        table = self.bpf["targets"]
        live = thread_ids(self.pid)
        if len(live) > 1024:
            raise RuntimeError("target process exceeds the 1024-thread map limit")
        for key in list(table.keys()):
            if key.value not in live:
                del table[key]
        for tid in live:
            table[ct.c_uint(tid)] = ct.c_ubyte(1)

    def set_enabled(self, enabled):
        self.bpf["control"][ct.c_int(0)] = ct.c_uint(int(enabled))
        self.enabled = enabled

    def snapshot(self, usage):
        self.refresh_threads()
        table = self.bpf["counts"]
        totals = tuple(sum(int(x) for x in table[ct.c_int(i)]) for i in (0, 1))
        previous, self.last = self.last, totals
        if not self.enabled:
            return {}
        return {"switch_out": max(0, totals[0] - previous[0]),
                "switch_in": max(0, totals[1] - previous[1])}

    def close(self):
        if self.bpf is not None:
            self.bpf.cleanup()
            self.bpf = None


def make_collector(name, pid):
    return {"none": Collector, "resource": ResourceCollector, "bcc": BCCCollector}[name](pid)
