"""Dependency-free offline report. All plotted points come from windows.csv."""
import html
import json
from pathlib import Path


def write_report(folder, rows, summary):
    folder = Path(folder)
    baseline = summary["baseline_ms"]
    max_time = max(r["end_s"] for r in rows)
    max_latency = max(max(r["p95_ms"] for r in rows), baseline * 1.3) * 1.12
    left, top, width, height = 62, 25, 820, 245
    x = lambda t: left + t / max_time * width
    y = lambda v: top + height - v / max_latency * height
    bands = []
    for r in rows:
        if r["mode"] == "detailed":
            bands.append('<rect x="{:.1f}" y="25" width="{:.1f}" height="245" fill="#d9f2e9"/>'.format(
                x(r["start_s"]), max(1, x(r["end_s"]) - x(r["start_s"]))))
        if r["injected_fraction"] > 0:
            bands.append('<rect x="{:.1f}" y="275" width="{:.1f}" height="7" fill="#d38b35"/>'.format(
                x(r["start_s"]), max(1, x(r["end_s"]) - x(r["start_s"]))))
    grid = []
    for i in range(5):
        value = i * max_latency / 4
        grid.append('<line x1="62" x2="882" y1="{0:.1f}" y2="{0:.1f}" stroke="#e3e8ed"/>'.format(y(value)))
        grid.append('<text x="53" y="{:.1f}" text-anchor="end">{:.1f}</text>'.format(y(value) + 4, value))
    for i in range(6):
        t = max_time * i / 5
        grid.append('<text x="{:.1f}" y="302" text-anchor="middle">{:.1f}s</text>'.format(x(t), t))
    curves = []
    for metric, color in [("median_ms", "#2258b5"), ("p95_ms", "#7996ce")]:
        points = " ".join("{:.1f},{:.1f}".format(x(r["end_s"]), y(r[metric])) for r in rows)
        curves.append('<polyline points="{}" fill="none" stroke="{}" stroke-width="2.5"/>'.format(points, color))
    threshold_y = y(baseline * summary["config"]["enter_ratio"])
    table_rows = []
    for r in rows:
        count = r.get("switch_out", "") if summary["backend"] == "bcc" else r.get("voluntary_switches", "")
        table_rows.append('<tr><td>{:.1f}</td><td>{}</td><td>{:.3f}</td><td>{:.1f}</td><td>{}</td></tr>'.format(
            r["end_s"], html.escape(r["mode"]), r["median_ms"], r["worker_cpu_percent_one_core"], count))
    events = "".join('<li>{:.2f}s: {} → {} ({})</li>'.format(
        e["time_s"], e["from"], e["to"], e["reason"]) for e in summary["transitions"]) or '<li>No transition occurred.</li>'
    backend = html.escape(summary["backend"])
    warning = ("Real Linux eBPF counters requested. See verification.json for what has actually been tested."
               if summary["backend"] == "bcc" else
               "Portable resource counters; this run does not use eBPF. Detailed mode changes displayed counters, not resource.getrusage acquisition cost.")
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Edge AI profiler demo report</title>
<style>body{font:16px/1.65 system-ui,sans-serif;color:#1c2d43;background:#f2f5f9;margin:0;padding:28px}
main{max-width:980px;margin:auto}section{background:white;border:1px solid #dde4ec;border-radius:12px;padding:24px;margin:18px 0}
h1{margin-bottom:0}h2{font-size:20px}small{color:#52647c}.warning{border-left:5px solid #d38b35;padding:12px;background:#fff8eb}
svg{width:100%;height:auto;font:12px system-ui}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #e1e6ed;padding:7px}
code{background:#edf1f7;padding:2px 5px}a{color:#2258b5}.stats{display:flex;gap:28px;flex-wrap:wrap}</style>
<main><h1>自适应性能分析器 · 小样例</h1><small>Local experiment report / 本地实验报告</small>
<section><div class="stats"><span>Backend: <b>BACKEND</b></span><span>Baseline: <b>BASELINE ms</b></span>
<span>Detailed duty cycle: <b>DUTY%</b></span></div><p class="warning">WARNING</p>
<p>负载：WORKLOAD。干扰：INTERFERENCE。玩具模型为随机权重，不代表 MobileNet 或真实模型准确率。</p>
<p>此报告用于演示数据采集和模式切换；单次运行不能证明开销下降。</p></section>
<section><h2>推理耗时与模式变化</h2><svg viewBox="0 0 930 325" role="img" aria-label="Latency over time, with detailed-mode shading">
BANDS GRID <line x1="62" x2="882" y1="THRESHOLD" y2="THRESHOLD" stroke="#b44d58" stroke-dasharray="5 4"/>
CURVES <text x="10" y="16">ms</text></svg>
<p>深蓝：median；浅蓝：p95；红色虚线：进入阈值。绿色背景：该窗口的详细模式；橙条：包含干扰调用的窗口。</p>
<p>模式切换在窗口结束后生效，因此触发窗口仍可能显示 basic。</p></section>
<section><h2>切换记录</h2><ul>EVENTS</ul></section>
<section><h2>窗口统计</h2><small>CPU 以单核 100% 计；空白计数表示未采集，不能解释为零。BCC 显示 switch-out；resource 显示 voluntary switch。</small>
<table><thead><tr><th>Time / s</th><th>Mode</th><th>Median / ms</th><th>Worker CPU / %</th><th>Detail count</th></tr></thead><tbody>ROWS</tbody></table></section>
<section><h2>原始结果</h2><a href="windows.csv">windows.csv</a> · <a href="summary.json">summary.json</a>
<p>峰值 RSS 和 controller CPU 在原始日志中。RSS 为进程历史峰值，并非当前内存；不包含 BPF maps 的内核内存。</p></section></main></html>'''
    replacements = {"BACKEND": backend, "BASELINE": "{:.3f}".format(baseline),
                    "DUTY": "{:.1f}".format(summary["detailed_duty_percent"]),
                    "WARNING": html.escape(warning), "WORKLOAD": html.escape(summary["workload"]),
                    "INTERFERENCE": html.escape(summary["config"]["interference"]),
                    "BANDS": "".join(bands), "GRID": "".join(grid), "THRESHOLD": "{:.1f}".format(threshold_y),
                    "CURVES": "".join(curves), "EVENTS": events, "ROWS": "".join(table_rows)}
    # One pass, so input text cannot be interpreted as another template token.
    import re
    document = re.sub("|".join(re.escape(k) for k in replacements), lambda m: replacements[m.group()], document)
    (folder / "report.html").write_text(document, encoding="utf-8")
