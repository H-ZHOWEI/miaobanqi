# -*- coding: utf-8 -*-
"""端到端冒烟（真实进程）：验证“只有 A 从无到有才触发 B”。

场景：
  1) A 已在运行时才启动监控 → 不触发
  2) A 运行中再开一个实例（模拟用户再次双击）→ 不触发
  3) A 完全关闭后重新打开 → 触发一次

用法： python tests/smoke_coldstart.py
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil  # noqa: E402
from core import Action, Conditions, ConfigManager, MonitorEngine, Task, Trigger  # noqa: E402

PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.isfile(PYW):
    print("跳过：未找到 pythonw.exe")
    sys.exit(0)

# 系统里若已有同名进程会干扰判定，直接跳过避免误报
existing = sum(1 for p in psutil.process_iter(["name"])
               if (p.info.get("name") or "").lower() == "pythonw.exe")
if existing:
    print(f"跳过：系统中已有 {existing} 个 pythonw.exe 进程，会干扰测试")
    sys.exit(0)

logs = []
cfg = ConfigManager(os.path.join(tempfile.gettempdir(), "mbq-coldstart-smoke.json"))
task = Task(
    name="冷启动冒烟任务",
    trigger=Trigger(field="name", value="pythonw.exe", match="exact"),
    conditions=Conditions(frequency="on_close", debounce_ms=200, cooldown_sec=1),
    actions=[Action(type="notify", value="联动成功")],
    enabled=True,
)
engine = MonitorEngine(cfg, lambda lv, msg: logs.append((lv, msg)))

procs = []


def wait(sec):
    time.sleep(sec)


def launch():
    p = subprocess.Popen([PYW, "-c", "import time; time.sleep(40)"])
    procs.append(p)
    wait(1.0)
    return p


try:
    # 阶段 1：A 先运行，监控后启动（A 已运行）
    launch()
    engine.apply_tasks([task], prime=True)
    engine.start()
    wait(2.5)
    s1 = task.fire_count
    print(f"阶段1 A已运行 → 触发次数 {s1}（期望 0）")

    # 阶段 2：A 仍运行，再开一个实例
    launch()
    wait(2.5)
    s2 = task.fire_count
    print(f"阶段2 A已运行+新实例 → 触发次数 {s2}（期望 0）")

    # 阶段 3：A 完全关闭
    for p in procs:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            pass
    procs.clear()
    wait(2.5)  # 等引擎观察到“完全消失”

    # 阶段 4：A 重新打开 → 应触发
    launch()
    deadline = time.time() + 6
    while time.time() < deadline and task.fire_count == 0:
        wait(0.2)
    s3 = task.fire_count
    print(f"阶段3 A关闭后重开 → 触发次数 {s3}（期望 1）")

    ok = (s1 == 0 and s2 == 0 and s3 == 1)
    print("关键日志:")
    for lv, msg in logs:
        if lv in ("触发", "完成", "错误"):
            print("  ", lv, msg)
    print("结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
finally:
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    engine.stop()
