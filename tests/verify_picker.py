# -*- coding: utf-8 -*-
"""无界面自动化验证：任务编辑器 → 进程选择器能正常打开并列出进程。
用法： python tests/verify_picker.py
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ui  # noqa: E402

root = tk.Tk()
root.withdraw()

# 拦截 Toplevel.wait_window，让对话框创建后立即做断言再关闭
created = []
orig_wait = tk.Toplevel.wait_window


def fake_wait(self, *args, **kwargs):
    created.append(self)
    if isinstance(self, ui.ProcessPicker):
        rows = self._filtered
        assert len(rows) > 5, f"进程列表过少: {len(rows)}"
        assert self.tree.get_children(), "列表控件为空"
        sample = rows[0]
        assert "name" in sample and "pid" in sample, sample
        print(f"进程选择器打开成功，显示 {len(rows)} 条进程")
        print("样例:", sample["name"], sample["pid"], sample["title"][:30])
        # 模拟选择第一项
        self.tree.selection_set(self.tree.get_children()[0])
        self.fill_mode.set("进程名（精确匹配，推荐）")
        self._choose()
        print("选择结果:", self.result)
        assert self.result and self.result["field"] == "name"
    else:
        self.destroy()


tk.Toplevel.wait_window = fake_wait

try:
    picker = ui.ProcessPicker(root)
    assert picker.result, "未产生选择结果"
finally:
    tk.Toplevel.wait_window = orig_wait
    root.destroy()

print("PASS")
