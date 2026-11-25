# -*- coding: utf-8 -*-
"""
喵伴启 - 程序入口
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import APP_NAME, APP_VERSION  # noqa: E402


def main():
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
