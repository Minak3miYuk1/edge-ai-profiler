"""A real toy neural-network forward pass, or a supplied ONNX model.

The toy network has seeded RANDOM weights: no training or accuracy claim.
Default slowdown is a deliberately inserted sleep, NOT CPU contention.
"""
import math
import multiprocessing as mp
import os
import random
import resource
import signal
import statistics
import sys
import time
import traceback


class TinyMLP:
    def __init__(self, repeats):
        rng = random.Random(42)
        self.x = [rng.uniform(-1, 1) for _ in range(32)]
        self.w1 = [[rng.uniform(-0.2, 0.2) for _ in range(32)] for _ in range(48)]
        self.w2 = [[rng.uniform(-0.2, 0.2) for _ in range(48)] for _ in range(8)]
        self.repeats = repeats

    def infer(self):
        for _ in range(self.repeats):
            hidden = [max(0.0, sum(w * x for w, x in zip(row, self.x))) for row in self.w1]
            output = [sum(w * h for w, h in zip(row, hidden)) for row in self.w2]
        return output[0]


class ONNXModel:
    def __init__(self, path, shape):
        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("ONNX mode needs numpy and onnxruntime; see requirements-onnx.txt") from exc
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        inputs = self.session.get_inputs()
        if len(inputs) != 1 or inputs[0].type != "tensor(float)":
            raise ValueError("sample supports one float32 ONNX input only")
        spec = inputs[0]
        dims = shape if shape else spec.shape
        if not dims or not all(isinstance(n, int) and n > 0 for n in dims):
            raise ValueError("dynamic model input requires --input-shape, e.g. 1,3,224,224")
        if len(dims) != len(spec.shape) or any(isinstance(n, int) and n > 0 and n != m
                                               for n, m in zip(spec.shape, dims)):
            raise ValueError("--input-shape conflicts with the model's fixed dimensions")
        if math.prod(dims) > 10_000_000:
            raise ValueError("sample input is limited to ten million float32 values")
        self.input = {spec.name: np.random.default_rng(42).normal(size=dims).astype(np.float32)}

    def infer(self):
        outputs = self.session.run(None, self.input)
        return float(outputs[0].flat[0])


def usage():
    r = resource.getrusage(resource.RUSAGE_SELF)
    # Linux reports KiB; macOS reports bytes. This is PEAK RSS, not current RSS.
    return {"cpu_s": r.ru_utime + r.ru_stime,
            "peak_rss_mb": r.ru_maxrss / (1024 ** 2 if sys.platform == "darwin" else 1024),
            "voluntary": r.ru_nvcsw, "involuntary": r.ru_nivcsw}


def cpu_competitor(active, stop, cpu, ready):
    os.sched_setaffinity(0, {cpu})
    ready.send(True)
    ready.close()
    value = 1
    while not stop.is_set():
        if not active.wait(0.05):
            continue
        for _ in range(20000):
            value = (value * 1664525 + 1013904223) & 0xFFFFFFFF


def batch(model, seconds, origin=None, config=None, active=None):
    start = time.monotonic()
    latencies, injected, checksum = [], 0, 0.0
    while time.monotonic() - start < seconds:
        slow = False
        if origin is not None:
            t = time.monotonic() - origin
            slow = config["slow_start"] <= t < config["slow_start"] + config["slow_duration"]
        if active is not None:
            if slow:
                active.set()
            else:
                active.clear()
        before = time.perf_counter_ns()
        result = model.infer()
        if slow and config["interference"] == "delay":
            time.sleep(config["slow_delay_ms"] / 1000)
        latencies.append((time.perf_counter_ns() - before) / 1e6)
        checksum = (checksum + result) % 1000000
        injected += int(slow)
    elapsed = time.monotonic() - start
    ordered = sorted(latencies)
    return {"median_ms": statistics.median(latencies),
            "p95_ms": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
            "calls": len(latencies), "seconds": elapsed,
            "throughput": len(latencies) / elapsed,
            "injected_fraction": injected / len(latencies), "checksum": checksum}


def worker(connection, config):
    def stop_worker(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop_worker)
    competitor = None
    active = stop = None
    try:
        if config["interference"] == "cpu":
            cpu = min(os.sched_getaffinity(0))
            os.sched_setaffinity(0, {cpu})
            ctx = mp.get_context("spawn")
            active, stop = ctx.Event(), ctx.Event()
            parent_ready, child_ready = ctx.Pipe()
            competitor = ctx.Process(target=cpu_competitor, args=(active, stop, cpu, child_ready))
            competitor.start()
            child_ready.close()
            if not parent_ready.poll(15):
                raise RuntimeError("CPU competitor did not start")
            parent_ready.recv()
            parent_ready.close()
        model = ONNXModel(config["onnx"], config["input_shape"]) if config["onnx"] else TinyMLP(config["repeats"])
        for _ in range(10):
            if not math.isfinite(model.infer()):
                raise RuntimeError("model produced a non-finite first output")
        calibrated = batch(model, config["calibration"])
        connection.send({"kind": "ready", "pid": os.getpid(), "calibration": calibrated, "usage": usage()})
        if connection.recv() != "start":
            return
        origin = time.monotonic()
        while time.monotonic() - origin < config["duration"]:
            remaining = config["duration"] - (time.monotonic() - origin)
            result = batch(model, min(config["window"], remaining), origin, config, active)
            result.update(kind="window", start_s=time.monotonic() - origin - result["seconds"],
                          end_s=time.monotonic() - origin, usage=usage())
            connection.send(result)
        connection.send({"kind": "done"})
        connection.recv()  # Stay alive until the parent has read the final BPF counters.
    except (BrokenPipeError, EOFError):
        pass
    except Exception:
        try:
            connection.send({"kind": "error", "message": traceback.format_exc()})
        except (BrokenPipeError, EOFError):
            pass
    finally:
        if stop is not None:
            stop.set()
        if competitor is not None:
            competitor.join(2)
            if competitor.is_alive():
                competitor.terminate()
                competitor.join()
        connection.close()
