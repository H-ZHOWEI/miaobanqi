# -*- coding: utf-8 -*-
"""
喵伴启 - 程序入口

单实例保护：重复双击 exe 不会启动第二个进程和第二个托盘图标，
而是把已在运行的实例窗口调到前台。
"""
import ctypes
import os
import sys
import traceback
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import APP_NAME, APP_VERSION  # noqa: E402

# Local\ 前缀保证只在本用户会话内互斥，不同用户互不干扰
_SINGLE_MUTEX = "Local\\MiaoBanQi_SingleInstance_v1"
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9


def acquire_single_instance():
    """获取单实例互斥体。返回 (句柄, 是否首个实例)。"""
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, True, _SINGLE_MUTEX)
        if not handle:
            return None, True
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None, False
        return handle, True
    except Exception:
        # 非 Windows 或 API 异常时退化成允许多实例，不影响主功能
        return None, True


def focus_existing_instance():
    """把已经在运行的实例窗口恢复并显示到前台。"""
    try:
        user32 = ctypes.windll.user32
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = []

        def cb(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 2)
                user32.GetWindowTextW(hwnd, buf, length + 2)
                if APP_NAME in buf.value:
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(enum_proc(cb), 0)
        if found:
            user32.ShowWindow(found[0], _SW_RESTORE)
            user32.SetForegroundWindow(found[0])
            return True
    except Exception:
        pass
    return False


def main():
    _mutex, is_first = acquire_single_instance()
    if not is_first:
        # 已有实例在跑：激活它的窗口后直接退出，不再注册托盘图标
        focus_existing_instance()
        return

    # 清理历史更新残留的 _MEI 临时目录（可能堆积到 GB 级）
    try:
        from updater import cleanup_stale_mei
        cleanup_stale_mei()
    except Exception:
        pass

    try:
        from ui import main as ui_main
        ui_main()
    except Exception:
        detail = traceback.format_exc()
        print(detail)
        try:
            import tkinter as tk
            from tkinter import messagebox
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(APP_NAME,
                                 f"程序启动失败。\n\n{detail[-1200:]}")
            r.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
