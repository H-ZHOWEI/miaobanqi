# -*- coding: utf-8 -*-
"""验证打包后的 exe 确实显示主窗口，而不是错误对话框。
用法： python tests/verify_exe.py
"""
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(HERE, "dist", "MiaoBanQi.exe")

user32 = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def visible_titles():
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 2)
                user32.GetWindowTextW(hwnd, buf, n + 2)
                if buf.value.strip():
                    out.append(buf.value)
        return True

    user32.EnumWindows(ENUM(cb), 0)
    return out


proc = subprocess.Popen([EXE])
try:
    time.sleep(5)
    titles = visible_titles()
    print("可见窗口：", titles)
    main_ok = any("喵伴启" in t for t in titles)
    err_bad = any(("启动失败" in t or "Traceback" in t) for t in titles)
    print(f"主窗口正常: {main_ok} | 出现错误窗口: {err_bad}")
    sys.exit(0 if main_ok and not err_bad else 1)
finally:
    try:
        proc.terminate()
        proc.wait(3)
    except Exception:
        proc.kill()
