#!/usr/bin/env python3
"""Run from this directory: python3 main.py --backend resource --out runs/demo"""
import argparse
import csv
import json
import math
import multiprocessing as mp
from pathlib import Path
import platform
import sys
import time

from edgeprof.collectors import make_collector
from edgeprof.policy import Policy
from edgeprof.report import write_report
from edgeprof.workload import usage, worker


def receive(connection, timeout):
    if not connection.poll(timeout):
        raise RuntimeError("workload timed out; no telemetry received")
    try:
        message = connection.recv()
    except EOFError as exc:
        raise RuntimeError("workload exited without a completion message") from exc
    if message["kind"] == "error":
        raise RuntimeError(message["message"])
    return message


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=["resource", "bcc", "none"], default="resource")
    p.add_argument("--mode", choices=["adaptive", "fixed", "off"], default="adaptive")
    p.add_argument("--duration", type=float, default=20)
    p.add_argument("--window", type=float, default=1)
    p.add_argument("--calibration", type=float, default=2)
    p.add_argument("--slow-start", type=float, default=4)
    p.add_argument("--slow-duration", type=float, default=6)
    p.add_argument("--slow-delay-ms", type=float, default=8)
    p.add_argument("--interference", choices=["delay", "cpu", "none"], default="delay")
    p.add_argument("--enter-ratio", type=float, default=1.2)
    p.add_argument("--exit-ratio", type=float, default=1.1)
    p.add_argument("--min-detailed", type=float, default=5)
    p.add_argument("--repeats", type=int, default=16, help="toy network forwards per timed inference")
    p.add_argument("--onnx", help="optional existing single-input float32 model path")
    p.add_argument("--input-shape", help="for dynamic ONNX input, e.g. 1,3,224,224")
    p.add_argument("--out", type=Path, default=Path("runs/demo"))
    args = p.parse_args()
    if sys.platform not in ("linux", "darwin"):
        p.error("sample supports Linux and macOS")
    for name in ("duration", "window", "calibration", "slow_start", "slow_duration", "slow_delay_ms", "min_detailed"):
        v = getattr(args, name)
        if not math.isfinite(v) or v < 0 or (name in ("duration", "window", "calibration") and v == 0):
            p.error("{} must be finite and {}".format(name, "positive" if name in ("duration", "window", "calibration") else "nonnegative"))
    if not (math.isfinite(args.enter_ratio) and math.isfinite(args.exit_ratio) and 1 <= args.exit_ratio < args.enter_ratio):
        p.error("require finite 1 <= exit-ratio < enter-ratio")
    if args.repeats < 1:
        p.error("--repeats must be positive")
    if args.backend == "bcc" and sys.platform != "linux":
        p.error("--backend bcc requires Linux; no simulated fallback is used")
    if args.interference == "cpu" and sys.platform != "linux":
        p.error("--interference cpu requires Linux CPU affinity support")
    if args.backend == "none" and args.mode != "off":
        p.error("--backend none requires --mode off")
    if args.mode == "off":
        args.backend = "none"  # No eBPF probes are loaded in the reference mode.
    if args.onnx:
        args.onnx = str(Path(args.onnx).resolve(strict=True))
    if args.input_shape:
        try:
            args.input_shape = [int(x) for x in args.input_shape.split(",")]
            if not args.input_shape or any(x <= 0 for x in args.input_shape):
                raise ValueError()
        except ValueError:
            p.error("--input-shape requires positive comma-separated integers")
    return args


def run(args):
    if args.out.exists() and any(args.out.iterdir()):
        raise RuntimeError("output directory is not empty; choose a new --out path")
    args.out.mkdir(parents=True, exist_ok=True)
    config = vars(args).copy()
    config["out"] = str(args.out.resolve())
    ctx = mp.get_context("spawn")
    connection, child = ctx.Pipe()
    process = ctx.Process(target=worker, args=(child, config))
    collector = None
    rows, transitions = [], []
    process.start()
    child.close()
    try:
        ready = receive(connection, 120)
        baseline = ready["calibration"]["median_ms"]
        policy = Policy(baseline, args.enter_ratio, args.exit_ratio, min_detailed_s=args.min_detailed)
        collector = make_collector(args.backend, ready["pid"])
        collector.snapshot(ready["usage"])
        mode = "detailed" if args.mode == "fixed" else "basic"
        collector.set_enabled(mode == "detailed")
        print("backend={} workload={} baseline={:.3f}ms".format(
            args.backend, "ONNX" if args.onnx else "random-weight toy MLP", baseline), flush=True)
        if args.backend == "resource":
            print("Portable OS counters, NOT eBPF. Default delay injection is artificial.", flush=True)
        last_cpu = ready["usage"]["cpu_s"]
        controller_start = usage()["cpu_s"]
        previous_snapshot = time.monotonic()
        connection.send("start")
        with (args.out / "windows.csv").open("w", newline="") as f:
            writer = None
            while True:
                message = receive(connection, max(30, args.window * 10))
                if message["kind"] == "done":
                    break
                before_cpu = time.process_time()
                details = collector.snapshot(message["usage"])
                snapshot_at = time.monotonic()
                interval = snapshot_at - previous_snapshot
                previous_snapshot = snapshot_at
                cpu = message["usage"]["cpu_s"]
                valid = message["calls"] >= 10 and message["seconds"] >= args.window * 0.9
                row = {"start_s": message["start_s"], "end_s": message["end_s"],
                       "mode": mode, "backend": args.backend, "next_mode": mode,
                       "median_ms": message["median_ms"], "p95_ms": message["p95_ms"],
                       "calls": message["calls"], "window_s": message["seconds"],
                       "throughput": message["throughput"], "baseline_ms": baseline,
                       "worker_cpu_percent_one_core": max(0, 100 * (cpu - last_cpu) / interval),
                       "worker_peak_rss_mb": message["usage"]["peak_rss_mb"],
                       "controller_peak_rss_mb": usage()["peak_rss_mb"],
                       "injected_fraction": message["injected_fraction"] if args.interference != "none" else 0,
                       "valid_policy_window": valid, "collector_interval_s": interval,
                       "voluntary_switches": "", "involuntary_switches": "",
                       "switch_out": "", "switch_in": ""}
                row.update(details)
                last_cpu = cpu
                if args.mode == "adaptive":
                    reason = policy.observe(message["median_ms"], message["end_s"], valid)
                    if reason:
                        transitions.append({"time_s": message["end_s"], "from": mode,
                                            "to": policy.mode, "reason": reason})
                        collector.set_enabled(policy.mode == "detailed")
                        mode = policy.mode
                row["next_mode"] = mode
                row["controller_window_cpu_ms"] = 1000 * (time.process_time() - before_cpu)
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                f.flush()
                rows.append(row)
                print("{:5.1f}s {:8s} median={:7.3f}ms next={}".format(
                    row["end_s"], row["mode"], row["median_ms"], mode), flush=True)
        collector.set_enabled(False)
        collector.close()
        collector = None
        connection.send("stop")
        process.join(5)
        if not rows:
            raise RuntimeError("no measurement windows produced")
        measured = sum(r["window_s"] for r in rows)
        detailed = sum(r["window_s"] for r in rows if r["mode"] == "detailed")
        summary = {"backend": args.backend, "config": config,
                   "workload": "supplied ONNX model" if args.onnx else "seeded random-weight toy MLP (untrained)",
                   "platform": platform.platform(), "python": sys.version,
                   "baseline_ms": baseline, "measured_seconds": measured,
                   "inferences": sum(r["calls"] for r in rows),
                   "detailed_duty_percent": 100 * detailed / measured,
                   "controller_cpu_s": usage()["cpu_s"] - controller_start,
                   "transitions": transitions,
                   "notes": ["One demonstration run, not an overhead experiment.",
                             "Resource backend always acquires rusage; detail mode only gates exported switch counters.",
                             "RSS values are peak RSS. BPF kernel memory and CPU are not included in controller usage.",
                             "BPF snapshots are cumulative reads across CPUs, not an atomic scheduler-event boundary."]}
        (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        write_report(args.out, rows, summary)
        print("Report: {}".format((args.out / "report.html").resolve()), flush=True)
        return summary
    finally:
        if collector is not None:
            collector.close()
        connection.close()
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(3)
        if process.is_alive():
            process.kill()
            process.join()


if __name__ == "__main__":
    try:
        run(parse_args())
    except KeyboardInterrupt:
        print("Interrupted; partial windows.csv may remain.", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, OSError, ValueError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        sys.exit(1)
