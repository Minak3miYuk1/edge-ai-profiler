# Edge AI Adaptive Profiler：可运行的小样例

这是前面 proposal 的最小原型：**运行推理 → 按窗口监测 → 变慢时启用更多统计 → 恢复后回到基础模式 → 导出结果**。

它适合向导师演示项目流程和讨论可行性。目前是教学原型，尚未完成 MobileNetV2 的正式实验，也不能据此声称减少了性能开销。

## 1. 最快运行：macOS / Linux，不需要安装 Python 包

需要 Python 3.9 或更新版本。从这个项目目录运行：

```bash
python3 main.py --out runs/my-first-demo
```

约 22 秒后打开 `runs/my-first-demo/report.html`。它是离线报告，不需要服务器。也可以使用：

```bash
sh run_demo.sh --out runs/another-demo
```

每次请选择不同的输出目录，避免覆盖数据。项目已经附有一次实际运行结果：`sample_results/report.html`、`windows.csv` 和 `summary.json`。具体测试范围见 `verification.json`。

默认流程如下：

1. 执行十次预热，再用两秒正常运行的耗时中位数校准基准。
2. 测量二十秒，每秒形成一个窗口。
3. 第 4–10 秒，在推理调用内部增加 8 ms 的人工延时，用于稳定演示变慢现象。
4. 连续两个有效窗口高于基准的 120%，下一窗口进入 `detailed`。
5. 详细模式保持至少五秒，连续三个窗口低于基准的 110% 后回到 `basic`。

默认干扰是 `sleep`，**这是人工延迟，不是 CPU 争用实验**。运行输出和报告都会注明。

## 2. 负载和采集后端分别是什么

默认负载是用 Python 写的 32 → 48 → 8 小型神经网络前向计算，使用固定随机权重，没有训练，不评价准确率，也不冒充 MobileNetV2。每次计时包含 `--repeats` 次前向计算；默认 16 次。它便于演示程序结构，不能代表高效推理框架的性能。

| 后端 | 运行环境 | 详细模式的数据 | 验证意义 |
|---|---|---|---|
| `resource`（默认） | macOS / Linux | `getrusage` 的自愿与非自愿上下文切换增量 | 演示控制流程，数据是真实 OS 统计；没有使用 eBPF |
| `bcc` | 支持 BCC 的 Linux | `sched_switch` 中目标线程的 switch-out / switch-in 计数 | 真实 eBPF 实现，需要在目标 Linux 系统加载验证 |
| `none` + `--mode off` | macOS / Linux | 无 | 保留同样的推理计时和基础资源统计作参考 |

`resource` 模式始终读取 `getrusage` 以得到 CPU 和峰值内存，模式切换只控制是否导出其中的切换计数。因此它不能证明采集成本降低。`bcc` 模式则在内核处理程序开头检查模式标志，关闭时跳过目标线程查询和计数，但探针分发仍有成本。不会在 BCC 失败时悄悄返回模拟数据。

## 3. 在 Linux 上试真实 eBPF

优先使用独立的 Ubuntu / Debian 测试机或虚拟机，需要允许内核 tracing，且安装与运行内核匹配的 headers。不同发行版的包名和内核配置可能不同。Ubuntu / Debian 的常见安装方式：

```bash
sudo apt update
sudo apt install python3-bpfcc bpfcc-tools linux-headers-$(uname -r)
sudo /usr/bin/python3 main.py --backend bcc --out runs/linux-bcc
```

请先使用系统 `/usr/bin/python3`，因为 apt 安装的 BCC 模块通常不会出现在另一个 Python 或普通虚拟环境中。不要用 PyPI 上同名的 `bcc` 包替代系统 BCC bindings。如果加载失败，程序会直接报告错误；优先核对 headers、BCC/LLVM、权限和 `sched:sched_switch` 是否可用。普通容器往往没有所需的 tracing 权限；macOS 容器里的 Linux 内核也不等于宿主 macOS 内核。

要把人工延时替换为真正的 CPU 争用，在 Linux 使用：

```bash
sudo /usr/bin/python3 main.py --backend bcc --interference cpu --out runs/linux-cpu
```

该选项把工作进程和一个由本程序管理的竞争进程绑定到同一个可用逻辑 CPU，只在指定时段让竞争进程忙算。竞争进程在校准期间等待，在退出时回收。它不修改系统调频器或其他全局配置。CPU 干扰是否足以触发规则取决于机器和推理耗时，不能保证每次都触发。

eBPF 程序位于 `bpf/sched.c`。它从事件中的 `prev_pid` / `next_pid` 判断目标线程，而非错误地使用当前执行探针的进程 ID。用户态在每个窗口刷新 `/proc/<pid>/task` 的线程列表；新线程在刷新前的事件、窗口内短命线程可能漏计。示例适用于线程集合基本稳定的推理进程。map 最多保存 1024 个线程，超过会报错。计数采用每 CPU 累加，不在读取后清空；跨 CPU 快照不是严格原子的，计数与推理窗口边界近似对齐，包含少量进程通信和采集启动边界行为。

## 4. 可选：接入你自己的真实 ONNX 模型

先安装可选依赖，再提供已有的单输入 float32 ONNX 模型：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-onnx.txt
.venv/bin/python main.py --onnx /path/to/model.onnx --out runs/onnx-demo
```

动态输入需要显式指定大小，例如 `--input-shape 1,3,224,224`。代码使用 ONNX Runtime CPU provider，固定单线程并关闭工作线程 spinning。输入为固定随机数据，只用于性能路径验证，不能用来验证分类准确率。模型输出的第一个数值会检查是否有限。

示例不自动下载模型；请记录自己选择模型的来源、版本、许可和校验值。多输入、非 float32 输入等模型目前会明确拒绝。BCC + ONNX 同时使用时，可在 Linux 用系统 Python 建立带 `--system-site-packages` 的虚拟环境，以保留发行版提供的 BCC 模块。该组合在本交付环境中未做端到端验证。

## 5. 输出怎么看

| 文件 | 内容 |
|---|---|
| `windows.csv` | 每窗口延迟、吞吐量、CPU、峰值 RSS、当前模式、下一模式和可用详细计数 |
| `summary.json` | 实际环境、参数、基准、切换时刻、详细模式占比和解释限制 |
| `report.html` | 离线折线图、模式背景、干扰标记和统计表 |

`mode` 是刚结束窗口使用的模式，`next_mode` 是观察完这个窗口之后应用的模式。首次触发的窗口仍可能标为 `basic`。最后不足一个完整周期或少于十次推理的窗口会记录，但不参与策略判断。较慢的模型应增大 `--window`。

CPU 以“单核 100%”计。工作进程 CPU 从自身累计 CPU 时间增量得到；controller CPU 不包含 eBPF 的内核执行时间。RSS 是进程历史峰值，不是当前占用，也不包括 BPF maps 的内核内存。基础模式下的详细列为空，含义是未采集，而不是发生了零次切换。BCC 的 switch-out 和 `getrusage` 的 voluntary switch 定义不同，不应直接当作同一指标比较。

调度切换次数不能单独证明瓶颈原因。报告中的“详细模式占比”只是测量时长占比，**不是开销减少百分比**。正式比较至少需要同一机器、相同模型配置、反复运行并交错安排三种模式：

```bash
python3 main.py --mode off --out runs/reference-01
python3 main.py --mode fixed --out runs/fixed-01
python3 main.py --mode adaptive --out runs/adaptive-01
```

这三条默认仍是 `resource` 演示；Linux 的真实实验请为 fixed/adaptive 加 `--backend bcc`，并固定干扰类型、模型、输入和 CPU 配置。校准是干净阶段，不纳入测量窗口；eBPF 在校准之后加载。

## 6. 代码与测试

```text
main.py                  命令行、子进程和结果组织
edgeprof/policy.py        两阈值、连续窗口、最短详细时长
edgeprof/workload.py      玩具网络、可选 ONNX、干扰与真实进程资源统计
edgeprof/collectors.py    resource / BCC / none 后端
edgeprof/report.py        离线报告
bpf/sched.c              真实 Linux eBPF 计数器
tests/                   策略和可运行演示测试
sample_results/          已执行的样例结果
verification.json        本次交付实际验证了什么
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖连续窗口触发、滞回、最短时长、非法窗口、计数差分、正常演示、关闭模式和 macOS 拒绝 BCC。真实 BPF verifier、tracepoint 计数和 CPU 竞争场景需在 Linux 另外验证，不能用这些跨平台测试代替。

另附一个默认跳过的真实 BCC 测试：在 Linux 上显式启用后，它会创建一个频繁休眠的目标进程，检查 switch-in/out 计数大于零，以及关闭模式标志后计数停止增加：

```bash
sudo env EDGEPROF_TEST_BCC=1 /usr/bin/python3 -m unittest discover -s tests -v
```

## 官方参考

- [BCC API reference](https://github.com/iovisor/bcc/blob/master/docs/reference_guide.md)
- [BCC installation](https://github.com/iovisor/bcc/blob/master/INSTALL.md)
- [ONNX Runtime Python API](https://onnxruntime.ai/docs/api/python/api_summary.html)

原 proposal 文件不需要修改；这个样例是对应实现的起点。
