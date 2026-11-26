# -*- coding: utf-8 -*-
"""端到端冒烟（真实窗口）：验证后台常驻程序“窗口从无到有”触发。

用记事本模拟：进程一直在，通过隐藏/显示窗口模拟 Edge 的后台常驻行为。
  - 窗口隐藏时启动监控 → 不触发
  - 窗口显示 → 触发一次
  - 保持显示 → 不重复触发
  - 隐藏再显示 → 再次触发
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Action, Conditions, ConfigManager, MonitorEngine, Task, Trigger  # noqa: E402

user32 = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
SW_HIDE = 0
SW_SHOW = 5

logs = []
fired = []
cfg = ConfigManager(os.path.join(tempfile.gettempdir(), "mbq-window-smoke.json"))
task = Task(
    name="窗口冷启动冒烟",
    trigger=Trigger(field="name", value="notepad.exe", match="exact"),
    conditions=Conditions(frequency="on_close", debounce_ms=300, cooldown_sec=1),
    actions=[Action(type="notify", value="ok")],
)
engine = MonitorEngine(cfg, lambda lv, msg: logs.append((lv, msg)),
                       on_task_fired=lambda t: fired.append(t.name))
engine._execute = lambda t: None


def notepad_windows():
    out = []

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            if "记事本" in buf.value or "Notepad" in buf.value:
                out.append(hwnd)
        return True

    user32.EnumWindows(ENUM(cb), 0)
    return out


def set_visible(hwnds, show):
    for h in hwnds:
        user32.ShowWindow(h, SW_SHOW if show else SW_HIDE)


proc = subprocess.Popen(["notepad.exe"])
try:
    deadline = time.time() + 8
    hwnds = []
    while time.time() < deadline and not hwnds:
        hwnds = notepad_windows()
        time.sleep(0.3)
    if not hwnds:
        print("跳过：未找到记事本窗口")
        sys.exit(0)

    # 预热：Windows 11 记事本首次显示时标题枚举有延迟，先显示一次再隐藏
    set_visible(hwnds, True)
    time.sleep(1.0)
    set_visible(hwnds, False)
    time.sleep(0.8)
    engine.apply_tasks([task], prime=True)
    engine.start()
    time.sleep(2.0)
    s1 = len(fired)
    print(f"阶段1 窗口隐藏 → 触发 {s1}（期望 0）")

    # 显示窗口
    set_visible(hwnds, True)
    deadline = time.time() + 4
    while time.time() < deadline and len(fired) == 0:
        time.sleep(0.2)
    s2 = len(fired)
    print(f"阶段2 窗口出现 → 触发 {s2}（期望 1）")

    # 保持显示
    time.sleep(2.0)
    s3 = len(fired)
    print(f"阶段3 窗口保持显示 → 触发 {s3}（期望 1）")

    # 隐藏再显示
    set_visible(hwnds, False)
    time.sleep(2.0)
    set_visible(hwnds, True)
    deadline = time.time() + 4
    while time.time() < deadline and len(fired) < 2:
        time.sleep(0.2)
    s4 = len(fired)
    print(f"阶段4 关窗再开 → 触发 {s4}（期望 2）")

    ok = (s1 == 0 and s2 == 1 and s3 == 1 and s4 == 2)
    print("关键日志:", [m for _, m in logs if "触发" in m or "窗口" in m])
    print("结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
finally:
    engine.stop()
    try:
        proc.terminate()
        proc.wait(5)
    except Exception:
        pass
