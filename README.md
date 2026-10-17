# Edge AI Adaptive Profiler

A minimal prototype of an adaptive performance profiler for edge AI workloads:

**Run inference → monitor each window → collect more detail during slowdowns → return to basic mode after recovery → export the results.**

This project is intended for an undergraduate project demonstration and a supervisor's feasibility review. It has not completed a formal MobileNetV2 evaluation or established a reduction in profiling overhead.

## 1. Quick start: macOS or Linux

The default demo requires Python 3.9 or later and no additional Python packages. Run the following from the project directory:

```bash
python3 main.py --out runs/my-first-demo
```

After approximately 22 seconds, open `runs/my-first-demo/report.html` in a browser. The report works offline and does not require a server. Alternatively, use:

```bash
sh run_demo.sh --out runs/another-demo
```

Choose a new output directory for each run to avoid overwriting results. An actual demonstration run is included in `sample_results/`, with `report.html`, `windows.csv`, and `summary.json`. See `verification.json` for the scope of the completed verification.

The default sequence is:

1. Perform ten warm-up calls, then establish a baseline using the median latency of two seconds of clean execution.
2. Measure for twenty seconds, using one-second windows.
3. Between seconds 4 and 10, insert an artificial 8 ms delay inside the timed inference operation to demonstrate a slowdown.
4. Enter `detailed` mode for the next window after two consecutive valid windows exceed 120% of the baseline.
5. Stay in detailed mode for at least five seconds, then return to `basic` mode after three consecutive windows fall below 110% of the baseline.

The default interference uses `sleep`: **it is an artificial delay, not a CPU-contention experiment**. Both the console output and the report identify this behavior.

## 2. Workloads and collection backends

The default workload is a small 32 → 48 → 8 neural-network forward pass implemented in Python. It uses fixed random weights, is untrained, and makes no accuracy claims. It is not MobileNetV2. Each timed inference includes `--repeats` forward passes, with a default of 16. This workload demonstrates the program structure; it does not represent the performance of an optimized inference framework.

| Backend | Environment | Detailed-mode data | Purpose |
|---|---|---|---|
| `resource` (default) | macOS / Linux | Changes in voluntary and involuntary context-switch counts from `getrusage` | Demonstrates the control flow using real OS statistics; does not use eBPF |
| `bcc` | Linux with BCC support | Target-thread switch-out and switch-in counts from `sched_switch` | Implements real eBPF collection; requires loading and validation on the target Linux system |
| `none` with `--mode off` | macOS / Linux | No detailed counters | Retains inference timing and basic resource statistics as a reference |

The `resource` backend always calls `getrusage` to obtain CPU time and peak memory usage. Mode changes only control whether its context-switch counters are exported, so this backend cannot demonstrate a reduction in collection cost. The `bcc` backend checks a mode flag at the beginning of the kernel handler and skips target-thread lookups and counting when disabled. Probe dispatch still has a cost. BCC failures are reported explicitly; the program does not silently substitute simulated data.

## 3. Run real eBPF collection on Linux

Use a Linux test machine or virtual machine, preferably Ubuntu or Debian, with kernel tracing permitted and headers matching the running kernel. Package names and kernel configuration vary between distributions. A common Ubuntu / Debian setup is:

```bash
sudo apt update
sudo apt install python3-bpfcc bpfcc-tools linux-headers-$(uname -r)
sudo /usr/bin/python3 main.py --backend bcc --out runs/linux-bcc
```

Start with the system `/usr/bin/python3`, because BCC modules installed through apt may not be available to another Python installation or an ordinary virtual environment. Do not substitute the similarly named PyPI `bcc` package for the system BCC bindings. If loading fails, the program reports an error. Check the kernel headers, BCC/LLVM installation, permissions, and availability of `sched:sched_switch`. Ordinary containers often lack the required tracing permissions, and a container's Linux kernel does not provide access to the macOS host kernel.

To replace the artificial delay with actual CPU contention on Linux, use:

```bash
sudo /usr/bin/python3 main.py --backend bcc --interference cpu --out runs/linux-cpu
```

This option pins the workload and a managed competitor process to the same available logical CPU. The competitor performs busy work only during the configured interference interval, waits during calibration, and is cleaned up on exit. The program does not change the system CPU governor or other global settings. Whether contention triggers the policy depends on the machine and inference duration; a transition is not guaranteed.

The eBPF program is in `bpf/sched.c`. It identifies target threads through the event's `prev_pid` and `next_pid` fields, rather than the ID of the process currently executing the probe. User space refreshes the thread list from `/proc/<pid>/task` once per window. Events from newly created threads before a refresh, or from short-lived threads between refreshes, may be missed. The example is therefore best suited to inference processes with a mostly stable thread set. The target map supports up to 1,024 threads and reports an error if that limit is exceeded. Counters accumulate per CPU without being cleared after reads. Cross-CPU snapshots are not strictly atomic, and their boundaries only approximately align with inference windows. Counts can include some interprocess communication and collection-start boundary activity.

## 4. Optional: use your own ONNX model

Install the optional dependencies and supply an existing ONNX model with a single float32 input:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-onnx.txt
.venv/bin/python main.py --onnx /path/to/model.onnx --out runs/onnx-demo
```

For a dynamic input shape, specify dimensions explicitly, for example with `--input-shape 1,3,224,224`. The implementation uses the ONNX Runtime CPU execution provider with a single-thread configuration and worker spinning disabled. Inputs use fixed random data for performance-path validation, not classification-accuracy evaluation. The first output value is checked for finiteness during warm-up.

The demo does not download a model automatically. Record the source, version, license, and checksum of the model you choose. Models with multiple inputs or non-float32 inputs are currently rejected. To use BCC and ONNX together on Linux, consider creating a virtual environment from the system Python with `--system-site-packages` so that the distribution's BCC bindings remain available. This combination has not been verified end to end in the delivery environment.

## 5. Understand the outputs

| File | Contents |
|---|---|
| `windows.csv` | Per-window latency, throughput, CPU usage, peak RSS, current mode, next mode, and available detailed counters |
| `summary.json` | Environment, parameters, baseline, transition times, detailed-mode duty cycle, and interpretation limits |
| `report.html` | Offline latency chart, mode shading, interference markers, and a statistics table |

`mode` describes the window that has just ended. `next_mode` describes the mode selected after observing that window. The first window that triggers a transition may therefore still be labeled `basic`. A final window shorter than 90% of the configured window duration, or any window with fewer than ten inferences, is recorded but excluded from policy decisions. Increase `--window` for slower models.

CPU usage is expressed relative to one logical CPU at 100%. Workload CPU usage comes from changes in the process's cumulative CPU time. Controller CPU usage excludes eBPF execution in the kernel. RSS is the process's historical peak, not its current memory usage, and does not include kernel memory allocated to BPF maps. Empty detailed columns in basic mode mean that data was not collected; they do not mean zero switches occurred. BCC switch-out counts and `getrusage` voluntary-switch counts have different definitions and should not be compared as identical metrics.

Context-switch counts alone cannot establish the cause of a bottleneck. The report's detailed-mode duty cycle is a fraction of measured time, **not a percentage reduction in overhead**. A formal comparison requires repeated runs on the same machine with the same model configuration and an interleaved order of the three modes:

```bash
python3 main.py --mode off --out runs/reference-01
python3 main.py --mode fixed --out runs/fixed-01
python3 main.py --mode adaptive --out runs/adaptive-01
```

These commands still use the `resource` demo by default. For a real Linux experiment, add `--backend bcc` to the fixed and adaptive runs, and hold the interference type, model, inputs, and CPU configuration constant. Calibration uses a clean phase and is excluded from the measurement windows. eBPF is loaded after calibration.

## 6. Project structure and tests

```text
main.py                  CLI, workload process, and result orchestration
edgeprof/policy.py        Thresholds, consecutive windows, and minimum detailed duration
edgeprof/workload.py      Toy network, optional ONNX, interference, and process statistics
edgeprof/collectors.py    resource / BCC / none backends
edgeprof/report.py        Offline report generation
bpf/sched.c              Linux eBPF scheduler counters
tests/                   Policy and runnable-demo tests
sample_results/          Results from an executed demonstration
verification.json        Scope of the completed verification
```

Run the tests:

```bash
python3 -m unittest discover -s tests -v
```

Tests cover consecutive-window triggering, hysteresis, minimum detailed duration, invalid windows, counter deltas, the runnable demo, off mode, and rejection of BCC on macOS. The BPF verifier, real tracepoint counts, and CPU-contention behavior require separate Linux validation; the portable tests do not replace those checks.

A real BCC test is included but skipped by default. When explicitly enabled on Linux, it creates a target process that sleeps frequently, checks that switch-in and switch-out counts are positive, and verifies that counting stops after the detailed-mode flag is disabled:

```bash
sudo env EDGEPROF_TEST_BCC=1 /usr/bin/python3 -m unittest discover -s tests -v
```

## Official references

- [BCC API reference](https://github.com/iovisor/bcc/blob/master/docs/reference_guide.md)
- [BCC installation](https://github.com/iovisor/bcc/blob/master/INSTALL.md)
- [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)

This prototype is a starting point for the proposed undergraduate project: demonstrate the control flow first, then validate Linux collection and integrate a trained model.
