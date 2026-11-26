# -*- coding: utf-8 -*-
"""验证单实例保护：重复启动不会堆积后台进程和托盘图标。

用法： python tests/verify_single_instance.py [exe路径]
不传参数时用源码 pythonw main.py 验证。
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import psutil

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = sys.argv[1] if len(sys.argv) > 1 else None
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

user32 = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_with_app():
    out = []

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 2)
            user32.GetWindowTextW(hwnd, buf, n + 2)
            if "喵伴启" in buf.value:
                out.append(buf.value)
        return True

    user32.EnumWindows(ENUM(cb), 0)
    return out


def launch():
    if EXE:
        return subprocess.Popen([EXE])
    if not os.path.isfile(PYW):
        print("跳过：未找到 pythonw.exe")
        sys.exit(0)
    return subprocess.Popen([PYW, os.path.join(HERE, "main.py")], cwd=HERE)


procs = []
try:
    print("启动第一个实例...")
    p1 = launch()
    procs.append(p1)

    deadline = time.time() + 20
    while time.time() < deadline and not windows_with_app():
        time.sleep(0.5)
    first_windows = windows_with_app()
    print("第一个实例窗口:", first_windows)

    print("启动第二个实例...")
    p2 = launch()
    procs.append(p2)
    # 第二个实例应自行退出
    deadline = time.time() + 8
    while time.time() < deadline and p2.poll() is None:
        time.sleep(0.3)
    second_exited = p2.poll() is not None
    print("第二个实例已退出:", second_exited, "| 退出码:", p2.poll())

    time.sleep(1)
    windows = windows_with_app()
    print("当前窗口数:", len(windows), windows)

    # 注意：PyInstaller 单文件 exe 正常就是一个逻辑实例对应 2 个进程
    # （bootloader 父进程 + 实际程序子进程），因此不能用进程数当实例数。
    # 判定标准：第二个 exe 快速自行退出 + 全程只有 1 个主窗口。
    ok = (second_exited and len(windows) == 1 and len(first_windows) == 1)
    print("结果:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
finally:
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
                p.wait(5)
        except Exception:
            pass
