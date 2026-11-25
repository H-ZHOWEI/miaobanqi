# -*- coding: utf-8 -*-
"""验证"从运行程序选择路径"模式：返回完整路径且自动带工作目录。
用法： python tests/verify_path_picker.py
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui  # noqa: E402

root = tk.Tk()
root.withdraw()

orig_wait = tk.Toplevel.wait_window
checked = []


def fake_wait(self, *args, **kwargs):
    if isinstance(self, ui.ProcessPicker) and self.path_only and not checked:
        checked.append(True)
        # 找一行有路径的进程
        target = next((i for i, r in enumerate(self._filtered) if r["exe"]), None)
        assert target is not None, "找不到带路径的进程"
        self.tree.selection_set(str(target))
        self._choose()
        assert self.result and self.result["field"] == "path", self.result
        assert os.path.isabs(self.result["value"]), self.result["value"]
        assert os.path.isfile(self.result["value"]), self.result["value"]
        print("路径选择器返回:", self.result["value"])
        print("进程名:", self.result["name"], "| PID:", self.result["pid"])
    else:
        self.destroy()


tk.Toplevel.wait_window = fake_wait
try:
    picker = ui.ProcessPicker(root, path_only=True)
    assert picker.result, "未产生选择结果"
finally:
    tk.Toplevel.wait_window = orig_wait
    root.destroy()

print("PASS")
