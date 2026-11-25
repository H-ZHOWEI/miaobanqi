# -*- coding: utf-8 -*-
"""端到端冒烟：启动一个后台进程模拟软件A，验证联动引擎确实触发。
用法： python tests/smoke_monitor.py
"""
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Action, Conditions, ConfigManager, Task, Trigger  # noqa: E402
from core import MonitorEngine  # noqa: E402

PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
if not os.path.isfile(PYW):
    print("跳过：未找到 pythonw.exe")
    sys.exit(0)

logs = []
cfg = ConfigManager(os.path.join(tempfile.gettempdir(), "mbq-smoke-config.json"))
task = Task(
    name="冒烟测试任务",
    trigger=Trigger(field="name", value="pythonw.exe", match="exact"),
    conditions=Conditions(frequency="on_close", debounce_ms=100, cooldown_sec=1),
    actions=[Action(type="notify", value="联动成功")],
    enabled=True,
)
cfg.tasks = [task]
engine = MonitorEngine(cfg, lambda lv, msg: logs.append((lv, msg)))
engine.apply_tasks([task], prime=True)
engine.start()

proc = subprocess.Popen([PYW, "-c", "import time; time.sleep(6)"])
try:
    deadline = time.time() + 6
    while time.time() < deadline and task.fire_count == 0:
        time.sleep(0.2)
    ok = task.fire_count > 0
    print("触发次数:", task.fire_count)
    print("关键日志:")
    for lv, msg in logs:
        if lv in ("触发", "完成", "错误"):
            print("  ", lv, msg)
    print("结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
finally:
    try:
        proc.terminate()
    except Exception:
        pass
    engine.stop()
