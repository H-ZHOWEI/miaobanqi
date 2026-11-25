# -*- coding: utf-8 -*-
"""验证所有下拉框显示为中文、内部值为英文 key。
用法： python tests/verify_i18n.py
"""
import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ACTION_TYPES, FREQUENCY_MODES, MATCH_MODES, SHOW_MODES, TRIGGER_FIELDS  # noqa: E402
from ui import MappedCombo  # noqa: E402

root = tk.Tk()
root.withdraw()

for name, mapping in [
    ("动作类型", ACTION_TYPES), ("监控对象", TRIGGER_FIELDS),
    ("匹配方式", MATCH_MODES), ("触发频率", FREQUENCY_MODES),
    ("窗口模式", SHOW_MODES),
]:
    first_key = next(iter(mapping))
    cb = MappedCombo(root, mapping, first_key, state="readonly")
    labels = cb.cget("values")
    assert all(any(ord(ch) > 127 for ch in lab) for lab in labels), \
        f"{name} 仍有英文选项: {labels}"
    assert cb.get() == first_key, f"{name} 初始值不正确"
    # 模拟用户选择第二项
    second_label = labels[1]
    second_key = list(mapping.values()).index(second_label)
    actual_key = list(mapping.keys())[second_key]
    cb.set(actual_key)
    assert cb.display() == second_label and cb.get() == actual_key
    cb.destroy()
    print(f"{name}: {labels} —— 中文 OK，内部 key OK")

root.destroy()
print("PASS")
