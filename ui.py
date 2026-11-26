# -*- coding: utf-8 -*-
"""
喵伴启 - 图形界面
主窗口 / 任务编辑器 / 设置 / 日志 / 系统托盘 / 更新横幅
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Callable, List, Optional

from core import (APP_NAME, APP_TAGLINE, APP_VERSION, APP_EN, CONFIG_PATH,
                  ACTION_TYPES, FREQUENCY_MODES, MATCH_MODES, TRIGGER_FIELDS,
                  WEEKDAY_LABELS, Action, Conditions, ConfigManager, Task,
                  Trigger, now_str, process_list)
import updater

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    pystray = None
    Image = ImageDraw = None

IS_WIN = sys.platform == "win32"

# 配色（浅色主题）
BG = "#F4F6FA"
PANEL = "#FFFFFF"
TEXT = "#1F2937"
MUTED = "#6B7280"
ACCENT = "#EA862C"
ACCENT_DARK = "#C96C1B"
OK = "#2E9E63"
WARN = "#E08A20"
ERR = "#D64545"
LINE = "#E2E7EF"


def _font(size=10, bold=False):
    family = "Microsoft YaHei UI" if IS_WIN else "Segoe UI"
    return (family, size, "bold" if bold else "normal")


def make_icon_image():
    """纯绘制的简洁猫脸托盘图标，无外部素材依赖"""
    if Image is None:
        return None
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 12, 58, 60), fill=(234, 134, 44, 255))
    d.polygon([(10, 24), (16, 4), (28, 16)], fill=(234, 134, 44, 255))
    d.polygon([(54, 24), (48, 4), (36, 16)], fill=(234, 134, 44, 255))
    d.ellipse((21, 30, 26, 36), fill=(40, 40, 40, 255))
    d.ellipse((38, 30, 43, 36), fill=(40, 40, 40, 255))
    d.polygon([(29, 40), (35, 40), (32, 44)], fill=(90, 50, 30, 255))
    d.line([(8, 41), (22, 43)], fill=(90, 50, 30, 255), width=2)
    d.line([(8, 48), (22, 46)], fill=(90, 50, 30, 255), width=2)
    d.line([(56, 41), (42, 43)], fill=(90, 50, 30, 255), width=2)
    d.line([(56, 48), (42, 46)], fill=(90, 50, 30, 255), width=2)
    return img


class Tray:
    """系统托盘：显示/隐藏、暂停、检查更新、退出"""

    def __init__(self, app: "App"):
        self.app = app
        self.icon = None
        if pystray is None or make_icon_image() is None:
            return
        try:
            def show(_=None):
                app.root.after(0, app.show_window)

            def pause(_=None):
                app.root.after(0, app.toggle_pause)

            def check(_=None):
                app.root.after(0, lambda: app.check_update(silent=False))

            def quit_app(_=None):
                app.root.after(0, app.quit_app)

            menu = pystray.Menu(
                pystray.MenuItem("显示主界面", show, default=True),
                pystray.MenuItem("暂停/继续监控", pause),
                pystray.MenuItem("检查更新", check),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", quit_app),
            )
            self.icon = pystray.Icon(APP_EN, make_icon_image(),
                                     f"{APP_NAME} v{APP_VERSION}", menu)
            self.icon.run_detached()
        except Exception:
            self.icon = None

    def notify(self, title: str, message: str) -> None:
        if not self.icon:
            return
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 通用小组件
# ---------------------------------------------------------------------------
def label_entry(parent, text, initial="", width=44, row=0, col=0, browse=None):
    tk.Label(parent, text=text, font=_font(), bg=PANEL, fg=TEXT).grid(
        row=row, column=col, sticky="w", padx=(0, 8), pady=6)
    var = tk.StringVar(value=str(initial or ""))
    e = ttk.Entry(parent, textvariable=var, width=width, font=_font())
    e.grid(row=row, column=col + 1, sticky="ew", pady=6)
    if browse:
        ttk.Button(parent, text="浏览...", width=8,
                   command=lambda: browse(var)).grid(row=row, column=col + 2, padx=(6, 0))
    return var


def bound_entry(parent, text, var, row=0, col=0, width=44, browse=None):
    tk.Label(parent, text=text, font=_font(), bg=PANEL, fg=TEXT).grid(
        row=row, column=col, sticky="w", padx=(0, 8), pady=6)
    ttk.Entry(parent, textvariable=var, width=width, font=_font()).grid(
        row=row, column=col + 1, sticky="ew", pady=6)
    if browse:
        ttk.Button(parent, text="浏览...", width=8,
                   command=lambda: browse(var)).grid(row=row, column=col + 2, padx=(6, 0))


class MappedCombo(ttk.Combobox):
    """下拉框显示中文，内部值仍是英文 key（配置兼容）。

    get() 返回内部 key；set(key) 接受内部 key；显示文本由 mapping 决定。
    """

    def __init__(self, parent, mapping, initial, **kwargs):
        self._mapping = dict(mapping)
        self._labels = list(self._mapping.values())
        self._reverse = {v: k for k, v in self._mapping.items()}
        self._var = tk.StringVar()
        super().__init__(parent, textvariable=self._var, values=self._labels, **kwargs)
        self.set(initial)

    def get(self):
        text = self._var.get()
        return self._reverse.get(text, text)

    def set(self, key):
        self._var.set(self._mapping.get(key, key))

    def display(self):
        return self._var.get()


def label_combo(parent, text, values, initial, row=0, col=0, width=30, readonly=True):
    tk.Label(parent, text=text, font=_font(), bg=PANEL, fg=TEXT).grid(
        row=row, column=col, sticky="w", padx=(0, 8), pady=6)
    cb = MappedCombo(parent, values, initial, width=width,
                     state="readonly" if readonly else "normal", font=_font())
    cb.grid(row=row, column=col + 1, sticky="ew", pady=6)
    return cb


def browse_file(var, exe_only=False):
    types = [("可执行文件", "*.exe"), ("所有文件", "*.*")] if exe_only else [("所有文件", "*.*")]
    p = filedialog.askopenfilename(filetypes=types)
    if p:
        var.set(p)


def browse_dir(var):
    p = filedialog.askdirectory()
    if p:
        var.set(p)


# ---------------------------------------------------------------------------
# 运行中程序选择器（任务管理器风格）
# ---------------------------------------------------------------------------
class ProcessPicker(tk.Toplevel):
    """列出当前正在运行的程序，供用户直接选择，无需手打进程名"""

    def __init__(self, parent, path_only: bool = False):
        super().__init__(parent)
        self.path_only = path_only
        self.title("选择正在运行的程序（获取程序路径）" if path_only
                   else "选择正在运行的程序")
        self.geometry("900x600")
        self.minsize(780, 500)
        self.configure(bg=PANEL)
        self.transient(parent)
        self.result: Optional[dict] = None
        self._rows = process_list()
        self._filtered: List[dict] = []
        self._sort_col = "name"
        self._sort_rev = False
        self.keyword = tk.StringVar()
        self.only_window = tk.BooleanVar(value=True)  # 默认只看有窗口的（像任务管理器的"应用"）
        self.keyword.trace_add("write", lambda *_: self._refresh())

        self._build()
        self._refresh()
        self.grab_set()
        self.wait_window()

    def _build(self):
        top = ttk.Frame(self, padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="搜索进程名 / 窗口标题 / PID：", font=_font()).pack(side="left")
        ttk.Entry(top, textvariable=self.keyword, width=30, font=_font()).pack(side="left", padx=6)
        ttk.Checkbutton(top, text="仅显示有窗口的程序", variable=self.only_window,
                        command=self._refresh).pack(side="left", padx=10)
        ttk.Button(top, text="刷新", width=7, command=self._reload).pack(side="left")
        tk.Label(top, text="双击列表项可直接选择", bg=PANEL, fg=MUTED,
                 font=_font(9)).pack(side="right")

        mid = ttk.Frame(self, padding=(14, 4))
        mid.pack(fill="both", expand=True)
        cols = ("name", "pid", "title", "mem", "path")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=18)
        heads = {
            "name": ("名称", 180), "pid": ("PID", 70), "title": ("窗口标题", 220),
            "mem": ("内存(MB)", 90), "path": ("路径", 300),
        }
        for c in cols:
            self.tree.heading(c, text=heads[c][0],
                              command=lambda cc=c: self._sort(cc))
            self.tree.column(c, width=heads[c][1], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<Double-Button-1>", lambda _: self._choose())

        bottom = ttk.Frame(self, padding=(14, 10))
        bottom.pack(fill="x")
        if self.path_only:
            # 取路径模式：隐藏"填入方式"选择，自动取程序完整路径
            self.fill_mode = tk.StringVar(value="进程完整路径（精确匹配）")
            tk.Label(bottom, text="选中后自动填入该程序的完整路径",
                     bg=PANEL, font=_font(), fg=ACCENT_DARK).pack(side="left")
            tip = "提示：若某程序路径为空（系统进程或权限不足），请选择其他程序"
        else:
            tk.Label(bottom, text="选中后填入：", bg=PANEL, font=_font()).pack(side="left")
            self.fill_mode = tk.StringVar(value="进程名（精确匹配，推荐）")
            ttk.Combobox(bottom, textvariable=self.fill_mode, state="readonly", width=30,
                         values=["进程名（精确匹配，推荐）", "进程名（包含匹配）",
                                 "窗口标题（包含匹配）", "进程完整路径（精确匹配）"],
                         font=_font()).pack(side="left", padx=6)
            tip = "提示：关闭再打开程序后 PID 会变化，所以一般用进程名而不是 PID"
        ttk.Button(bottom, text="取消", command=self.destroy, width=9).pack(side="right", padx=6)
        ttk.Button(bottom, text="确定", command=self._choose, width=9).pack(side="right")
        tk.Label(bottom, text=tip, bg=PANEL, fg=MUTED, font=_font(9)).pack(side="left", padx=14)

    def _reload(self):
        self._rows = process_list()
        self._refresh()

    def _sort(self, col):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col, self._sort_rev = col, False
        self._refresh()

    def _refresh(self):
        kw = self.keyword.get().strip().lower()
        rows = [r for r in self._rows
                if (not self.only_window.get() or r["titles"])
                and (not kw or kw in r["name"].lower() or kw in r["title"].lower()
                     or kw in r["exe"].lower() or kw in str(r["pid"]))]
        key = {"name": "name", "pid": "pid", "title": "title", "mem": "mem", "path": "exe"}.get(
            self._sort_col, "name")
        rows.sort(key=lambda r: (str(r.get(key, "")).lower(), r["pid"]), reverse=self._sort_rev)
        self._filtered = rows
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), values=(
                r["name"] or "?", r["pid"], r["title"] or "—", r["mem"], r["exe"] or "—"))

    def _choose(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "请先在列表中选择一个程序", parent=self)
            return
        r = self._filtered[int(sel[0])]
        mode = self.fill_mode.get()
        if mode.startswith("进程名（精确"):
            self.result = {"field": "name", "value": r["name"], "match": "exact"}
        elif mode.startswith("进程名（包含"):
            self.result = {"field": "name", "value": r["name"], "match": "contains"}
        elif mode.startswith("窗口标题"):
            if not r["title"]:
                messagebox.showinfo(APP_NAME, "该进程当前没有可见窗口标题，请改用进程名", parent=self)
                return
            self.result = {"field": "title", "value": r["title"], "match": "contains"}
        else:
            if not r["exe"]:
                hint = "无法读取该进程路径（系统进程或权限不足），请换一个程序" if self.path_only \
                    else "无法读取该进程路径（系统进程或权限不足），请改用进程名"
                messagebox.showinfo(APP_NAME, hint, parent=self)
                return
            self.result = {"field": "path", "value": r["exe"], "match": "exact",
                           "pid": r["pid"], "name": r["name"]}
        self.destroy()


# ---------------------------------------------------------------------------
# 动作编辑器
# ---------------------------------------------------------------------------
class ActionEditor(tk.Toplevel):
    def __init__(self, parent, action: Optional[Action] = None):
        super().__init__(parent)
        self.title("编辑动作")
        self.resizable(False, False)
        self.configure(bg=PANEL)
        self.transient(parent)
        self.result: Optional[Action] = None
        a = action or Action()
        self._show_key = a.show  # 持久保存窗口模式，避免切换动作类型时丢失

        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)

        ttk.Label(root, text="动作类型").grid(row=0, column=0, sticky="w", pady=6)
        self._type = MappedCombo(root, ACTION_TYPES, a.type,
                                 state="readonly", width=30, font=_font())
        self._type.grid(row=0, column=1, sticky="ew", pady=6)
        self._type.bind("<<ComboboxSelected>>", lambda _: self._render())

        self.body = ttk.Frame(root)
        self.body.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.body.columnconfigure(1, weight=1)

        self.v_path = tk.StringVar(value=a.path)
        self.v_args = tk.StringVar(value=a.args)
        self.v_cwd = tk.StringVar(value=a.cwd)
        self.v_admin = tk.BooleanVar(value=a.admin)
        self.v_value = tk.StringVar(value=a.value)
        self.v_delay = tk.StringVar(value=str(a.delay_ms))
        self.v_note = tk.StringVar(value=a.note)

        self._render()

        btns = ttk.Frame(root)
        btns.grid(row=2, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="right", padx=6)
        ttk.Button(btns, text="确定", command=self._ok, width=10).pack(side="right")

        self.grab_set()
        self.wait_window()

    def _clear(self):
        for w in list(self.body.winfo_children()):
            w.destroy()

    def _pick_running_path(self):
        """从运行中的程序选择，自动填入该程序的完整路径"""
        picker = ProcessPicker(self, path_only=True)
        if not picker.result:
            return
        path = picker.result.get("value", "")
        self.v_path.set(path)
        # 若工作目录为空，自动用该程序所在目录（很多程序依赖相对路径）
        if path and not self.v_cwd.get().strip():
            self.v_cwd.set(os.path.dirname(path))

    def _render(self):
        self._clear()
        t = self._type.get()
        r = 0
        if t == "launch":
            # 程序路径：浏览文件 + 从运行中的程序选择（自动取完整路径）
            tk.Label(self.body, text="程序路径", font=_font(), bg=PANEL, fg=TEXT).grid(
                row=r, column=0, sticky="w", padx=(0, 8), pady=6)
            ttk.Entry(self.body, textvariable=self.v_path, width=42,
                      font=_font()).grid(row=r, column=1, sticky="ew", pady=6)
            btn_box = ttk.Frame(self.body)
            btn_box.grid(row=r, column=2, padx=(6, 0), sticky="w")
            ttk.Button(btn_box, text="浏览...", width=8,
                       command=lambda: browse_file(self.v_path, True)).pack(side="left")
            ttk.Button(btn_box, text="从运行程序选择", width=16,
                       command=self._pick_running_path).pack(side="left", padx=4)
            bound_entry(self.body, "启动参数", self.v_args, r + 1, width=42)
            bound_entry(self.body, "工作目录", self.v_cwd, r + 2, width=42,
                        browse=browse_dir)
            f = ttk.Frame(self.body)
            f.grid(row=r + 3, column=1, columnspan=2, sticky="w", pady=4)
            ttk.Checkbutton(f, text="以管理员身份运行", variable=self.v_admin).pack(side="left")
            ttk.Label(f, text="  窗口：").pack(side="left")
            from core import SHOW_MODES
            self.v_show = MappedCombo(f, SHOW_MODES, self._show_key,
                                      state="readonly", width=18)
            self.v_show.pack(side="left")
            self.v_show.bind("<<ComboboxSelected>>",
                             lambda _: setattr(self, "_show_key", self.v_show.get()))
            note_row = r + 4
        elif t in ("open", "notify", "kill"):
            names = {"open": "文件路径或网址", "notify": "提醒内容", "kill": "进程名（多个用逗号分隔）"}
            bound_entry(self.body, names[t], self.v_value, r, width=48,
                        browse=browse_file if t == "open" else None)
            if t == "open":
                tk.Label(self.body, text="提示：网址请填完整 http:// 或 https://",
                         bg=PANEL, fg=MUTED, font=_font(9)).grid(
                             row=r + 1, column=1, sticky="w")
            note_row = r + 2
        elif t == "delay":
            tk.Label(self.body, text="等待毫秒", font=_font(), bg=PANEL).grid(
                row=r, column=0, sticky="w", pady=6)
            ttk.Spinbox(self.body, from_=0, to=3600000, textvariable=self.v_delay,
                        width=14, font=_font()).grid(row=r, column=1, sticky="w", pady=6)
            tk.Label(self.body, text="1000 = 1 秒", bg=PANEL, fg=MUTED,
                     font=_font(9)).grid(row=r, column=2, sticky="w", padx=8)
            note_row = r + 1
        else:
            note_row = r

        bound_entry(self.body, "备注", self.v_note, note_row, width=44)

    def _ok(self):
        try:
            delay = int(float(self.v_delay.get() or 0))
        except ValueError:
            delay = 1000
        self.result = Action(
            type=self._type.get(),
            path=self.v_path.get().strip(),
            args=self.v_args.get().strip(),
            cwd=self.v_cwd.get().strip(),
            admin=self.v_admin.get(),
            show=self._show_key,
            value=self.v_value.get().strip(),
            delay_ms=max(0, delay),
            note=self.v_note.get().strip(),
        )
        self.destroy()


# ---------------------------------------------------------------------------
# 任务编辑器
# ---------------------------------------------------------------------------
class TaskEditor(tk.Toplevel):
    def __init__(self, parent, task: Optional[Task] = None, test_cb=None):
        super().__init__(parent)
        self.title("新建任务" if task is None else "编辑任务")
        self.geometry("760x620")
        self.minsize(700, 560)
        self.configure(bg=PANEL)
        self.transient(parent)
        self.result: Optional[Task] = None
        self.test_cb = test_cb
        t = task or Task()
        self.work = Task.from_dict(t.to_dict())  # 深拷贝

        self.v_name = tk.StringVar(value=self.work.name)
        self.v_enabled = tk.BooleanVar(value=self.work.enabled)
        self.v_note = tk.StringVar(value=self.work.note)

        self._build()
        self.grab_set()
        self.wait_window()

    def _build(self):
        top = ttk.Frame(self, padding=(16, 12))
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Checkbutton(top, text="启用", variable=self.v_enabled).grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.v_name, font=_font(11, True),
                  width=44).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Entry(top, textvariable=self.v_note, font=_font(),
                  width=44).grid(row=0, column=2, sticky="ew")

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=8)
        f1 = ttk.Frame(nb, padding=14)
        f2 = ttk.Frame(nb, padding=14)
        f3 = ttk.Frame(nb, padding=14)
        nb.add(f1, text="  触发条件  ")
        nb.add(f2, text="  执行动作  ")
        nb.add(f3, text="  高级条件  ")
        f1.columnconfigure(1, weight=1)
        f3.columnconfigure(1, weight=1)

        # --- 触发条件 ---
        tr = self.work.trigger
        tk.Label(f1, text="当以下程序/窗口出现时：", bg=PANEL, font=_font(10, True),
                 fg=ACCENT_DARK).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.v_field = label_combo(f1, "监控对象", TRIGGER_FIELDS, tr.field, 1, 0)
        self.v_value = label_entry(f1, "匹配内容", tr.value, 40, 2, 0)
        ttk.Button(f1, text="选择运行中的程序...", command=self._pick_process,
                   width=18).grid(row=2, column=2, padx=(8, 0))
        self.v_match = label_combo(f1, "匹配方式", MATCH_MODES, tr.match, 3, 0)
        self.v_title = label_entry(f1, "窗口标题包含（可选）", tr.title_filter, 40, 4, 0)
        self.v_cs = tk.BooleanVar(value=tr.case_sensitive)
        tk.Checkbutton(f1, text="区分大小写", variable=self.v_cs, bg=PANEL,
                       font=_font(9)).grid(row=5, column=1, sticky="w", pady=4)
        hints = [
            "进程名示例：notepad.exe（精确）或 chrome*（通配符）",
            "进程路径示例：C:\\Program Files\\App\\app.exe",
            "窗口标题示例：Chrome（包含匹配），适合按文档标题触发",
            "小技巧：点上方「选择运行中的程序...」可直接从列表挑选，不用手打",
        ]
        for i, h in enumerate(hints):
            tk.Label(f1, text="• " + h, bg=PANEL, fg=MUTED, font=_font(9)).grid(
                row=6 + i, column=0, columnspan=3, sticky="w", pady=1)

        # --- 执行动作 ---
        tk.Label(f2, text="按顺序执行以下动作：", bg=PANEL, font=_font(10, True),
                 fg=ACCENT_DARK).pack(anchor="w", pady=(0, 8))
        listframe = ttk.Frame(f2)
        listframe.pack(fill="both", expand=True)
        self.action_list = tk.Listbox(listframe, font=_font(), height=10,
                                      selectbackground=ACCENT, activestyle="none")
        self.action_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listframe, command=self.action_list.yview)
        sb.pack(side="right", fill="y")
        self.action_list.config(yscrollcommand=sb.set)
        self.action_list.bind("<Double-Button-1>", lambda _: self._edit_action())
        btns = ttk.Frame(f2)
        btns.pack(fill="x", pady=8)
        for txt, cmd in [("添加", self._add_action), ("编辑", self._edit_action),
                         ("删除", self._del_action), ("上移", lambda: self._move(-1)),
                         ("下移", lambda: self._move(1))]:
            ttk.Button(btns, text=txt, command=cmd, width=8).pack(side="left", padx=(0, 6))
        self._refresh_actions()

        # --- 高级条件 ---
        c = self.work.conditions
        self.v_notrun = label_entry(f3, "这些进程运行时不触发", c.require_not_running, 36, 0, 0)
        self.v_run = label_entry(f3, "仅这些进程运行时触发", c.require_running, 36, 1, 0)
        tk.Label(f3, text="多个进程名用英文逗号分隔", bg=PANEL, fg=MUTED,
                 font=_font(9)).grid(row=1, column=1, sticky="e")

        tf = ttk.Frame(f3)
        tf.grid(row=2, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Label(tf, text="时间窗口").pack(side="left")
        self.v_tstart = tk.StringVar(value=c.time_start)
        self.v_tend = tk.StringVar(value=c.time_end)
        ttk.Entry(tf, textvariable=self.v_tstart, width=8).pack(side="left", padx=(8, 4))
        ttk.Label(tf, text="至").pack(side="left")
        ttk.Entry(tf, textvariable=self.v_tend, width=8).pack(side="left", padx=4)
        ttk.Label(tf, text="（留空不限，支持跨午夜，如 22:00 至 06:00）",
                  foreground=MUTED).pack(side="left", padx=8)

        wf = ttk.Frame(f3)
        wf.grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(wf, text="生效星期").pack(side="left")
        self.week_vars = []
        preset = set(c.weekdays)
        for i, lab in enumerate(WEEKDAY_LABELS):
            v = tk.BooleanVar(value=(i in preset or not preset))
            self.week_vars.append(v)
            ttk.Checkbutton(wf, text="周" + lab, variable=v).pack(side="left", padx=2)

        self.v_freq = label_combo(f3, "触发频率", FREQUENCY_MODES, c.frequency, 4, 0)
        # StringVar 必须持久持有，否则会被 GC 回收导致输入值丢失
        self._sv_interval = tk.StringVar(value=str(c.interval_sec))
        self.v_interval = ttk.Spinbox(f3, from_=1, to=86400, width=12,
                                      textvariable=self._sv_interval, font=_font())
        self.v_interval.grid(row=5, column=1, sticky="w", pady=6)
        tk.Label(f3, text="间隔秒数（仅“定时触发”用）", bg=PANEL, fg=MUTED,
                 font=_font(9)).grid(row=5, column=2, sticky="w")
        self._sv_debounce = tk.StringVar(value=str(c.debounce_ms))
        self.v_debounce = ttk.Spinbox(f3, from_=0, to=30000, width=12,
                                      textvariable=self._sv_debounce, font=_font())
        self.v_debounce.grid(row=6, column=1, sticky="w", pady=6)
        tk.Label(f3, text="防抖毫秒（建议 300~800，避免短暂进程误触发）",
                 bg=PANEL, fg=MUTED, font=_font(9)).grid(row=6, column=2, sticky="w")
        self._sv_predelay = tk.StringVar(value=str(c.pre_delay_ms))
        self.v_predelay = ttk.Spinbox(f3, from_=0, to=300000, width=12,
                                      textvariable=self._sv_predelay, font=_font())
        self.v_predelay.grid(row=7, column=1, sticky="w", pady=6)
        tk.Label(f3, text="触发后延时执行毫秒", bg=PANEL, fg=MUTED,
                 font=_font(9)).grid(row=7, column=2, sticky="w")
        self._sv_cooldown = tk.StringVar(value=str(c.cooldown_sec))
        self.v_cooldown = ttk.Spinbox(f3, from_=0, to=86400, width=12,
                                      textvariable=self._sv_cooldown, font=_font())
        self.v_cooldown.grid(row=8, column=1, sticky="w", pady=6)
        tk.Label(f3, text="最小触发间隔秒", bg=PANEL, fg=MUTED,
                 font=_font(9)).grid(row=8, column=2, sticky="w")
        tk.Label(f3, text="触发规则：只有 A 从完全关闭到重新打开才算“打开 A”，此时才启动 B；"
                          "A 已在运行时再次点开、切回窗口都不会触发 B。",
                 bg=PANEL, fg=ACCENT, font=_font(9)).grid(
                     row=9, column=0, columnspan=3, sticky="w", pady=(10, 4))

        btns = ttk.Frame(self, padding=(16, 8))
        btns.pack(fill="x", side="bottom")
        ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="right", padx=6)
        ttk.Button(btns, text="保存", command=self._save, width=10).pack(side="right")
        if self.test_cb:
            ttk.Button(btns, text="测试动作", command=self._test, width=10).pack(side="right", padx=6)
        ttk.Label(btns, text="提示：双击动作可编辑", foreground=MUTED).pack(side="left")

    def _pick_process(self):
        picker = ProcessPicker(self)
        if picker.result:
            self.v_field.set(picker.result["field"])
            self.v_value.set(picker.result["value"])
            self.v_match.set(picker.result["match"])
            self.v_cs.set(False)

    # --- 动作列表 ---
    def _refresh_actions(self):
        self.action_list.delete(0, "end")
        for i, a in enumerate(self.work.actions, 1):
            self.action_list.insert("end", f"{i}. {a.summary()}")
        if self.work.actions:
            self.action_list.selection_set(0)

    def _add_action(self):
        ed = ActionEditor(self)
        if ed.result:
            self.work.actions.append(ed.result)
            self._refresh_actions()

    def _selected_index(self):
        sel = self.action_list.curselection()
        return sel[0] if sel else (len(self.work.actions) - 1 if self.work.actions else -1)

    def _edit_action(self):
        i = self._selected_index()
        if i < 0 or i >= len(self.work.actions):
            return
        ed = ActionEditor(self, self.work.actions[i])
        if ed.result:
            self.work.actions[i] = ed.result
            self._refresh_actions()
            self.action_list.selection_set(i)

    def _del_action(self):
        i = self._selected_index()
        if 0 <= i < len(self.work.actions):
            del self.work.actions[i]
            self._refresh_actions()

    def _move(self, delta):
        i = self._selected_index()
        j = i + delta
        if 0 <= i < len(self.work.actions) and 0 <= j < len(self.work.actions):
            self.work.actions[i], self.work.actions[j] = self.work.actions[j], self.work.actions[i]
            self._refresh_actions()
            self.action_list.selection_set(j)

    def _collect(self) -> Task:
        t = self.work
        t.name = self.v_name.get().strip()
        t.enabled = self.v_enabled.get()
        t.note = self.v_note.get().strip()
        t.trigger = Trigger(
            field=self.v_field.get(), value=self.v_value.get().strip(),
            match=self.v_match.get(), title_filter=self.v_title.get().strip(),
            case_sensitive=self.v_cs.get())
        weekdays = [i for i, v in enumerate(self.week_vars) if v.get()]
        if len(weekdays) == 7:
            weekdays = []
        def _n(w, default):
            try:
                return int(float(str(w.get())))
            except Exception:
                return default
        t.conditions = Conditions(
            require_not_running=self.v_notrun.get().strip(),
            require_running=self.v_run.get().strip(),
            time_start=self.v_tstart.get().strip(),
            time_end=self.v_tend.get().strip(),
            weekdays=weekdays,
            frequency=self.v_freq.get(),
            interval_sec=_n(self.v_interval, 60),
            debounce_ms=_n(self.v_debounce, 400),
            pre_delay_ms=_n(self.v_predelay, 0),
            cooldown_sec=_n(self.v_cooldown, 2),
            fire_if_already_running=False)  # 已移除该选项：仅冷启动才触发
        return t

    def _save(self):
        t = self._collect()
        errs = t.validate()
        if errs:
            messagebox.showwarning("请检查", "\n".join("• " + e for e in errs), parent=self)
            return
        self.result = t
        self.destroy()

    def _test(self):
        t = self._collect()
        if self.test_cb:
            self.test_cb(t)


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    KEY = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run"

    def __init__(self, parent, config: ConfigManager, log):
        super().__init__(parent)
        self.title("设置")
        self.geometry("620x560")
        self.resizable(False, False)
        self.configure(bg=PANEL)
        self.transient(parent)
        self.config_mgr = config
        self.log = log
        s = config.settings

        root = ttk.Frame(self, padding=18)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        ttk.Label(root, text="常规", font=_font(11, True), foreground=ACCENT_DARK).grid(
            row=0, column=0, columnspan=3, sticky="w")

        self.v_poll = tk.StringVar(value=str(s.get("poll_interval_ms", 800)))
        tk.Label(root, text="监控轮询间隔(毫秒)", font=_font(), bg=PANEL).grid(
            row=1, column=0, sticky="w", pady=6)
        ttk.Spinbox(root, from_=200, to=10000, increment=100,
                    textvariable=self.v_poll, width=12, font=_font()).grid(
                        row=1, column=1, sticky="w", pady=6)
        tk.Label(root, text="越小越灵敏，但占用略高；建议 500~1000", bg=PANEL,
                 fg=MUTED, font=_font(9)).grid(row=1, column=2, sticky="w")

        self.v_tray = tk.BooleanVar(value=bool(s.get("minimize_to_tray", True)))
        self.v_start = tk.BooleanVar(value=bool(s.get("start_with_windows", False)))
        self.v_notify = tk.BooleanVar(value=bool(s.get("show_notification", True)))
        self.v_sound = tk.BooleanVar(value=bool(s.get("sound_notify", False)))
        ttk.Checkbutton(root, text="关闭窗口时最小化到托盘（推荐常驻）",
                        variable=self.v_tray).grid(row=2, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Checkbutton(root, text="开机自动启动", variable=self.v_start).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Checkbutton(root, text="触发时显示系统通知", variable=self.v_notify).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=5)
        ttk.Checkbutton(root, text="触发时播放提示音", variable=self.v_sound).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=5)

        ttk.Label(root, text="更新源（按顺序并发检测，自动取最高版本）",
                  font=_font(10, True), foreground=ACCENT_DARK).grid(
                      row=6, column=0, columnspan=3, sticky="w", pady=(16, 6))
        self.url_list = tk.Listbox(root, height=5, font=_font(9))
        self.url_list.grid(row=7, column=0, columnspan=3, sticky="ew")
        for u in s.get("manifest_urls", []):
            self.url_list.insert("end", u)
        ub = ttk.Frame(root)
        ub.grid(row=8, column=0, columnspan=3, sticky="w", pady=6)
        ttk.Button(ub, text="添加", command=self._add_url, width=8).pack(side="left", padx=(0, 5))
        ttk.Button(ub, text="删除", command=self._del_url, width=8).pack(side="left", padx=(0, 5))
        ttk.Button(ub, text="恢复默认更新地址", command=self._reset_urls).pack(side="left")

        ttk.Label(root, text=f"配置文件：{CONFIG_PATH}", foreground=MUTED,
                  font=_font(9), wraplength=560).grid(row=9, column=0, columnspan=3,
                                                       sticky="w", pady=(14, 0))
        btns = ttk.Frame(root)
        btns.grid(row=10, column=0, columnspan=3, sticky="e", pady=14)
        ttk.Button(btns, text="取消", command=self.destroy, width=10).pack(side="right", padx=6)
        ttk.Button(btns, text="保存", command=self._save, width=10).pack(side="right")
        self.grab_set()
        self.wait_window()

    def _add_url(self):
        d = tk.Toplevel(self)
        d.title("添加更新源")
        d.geometry("520x130")
        d.configure(bg=PANEL)
        d.transient(self)
        v = tk.StringVar()
        ttk.Entry(d, textvariable=v, width=64, font=_font()).pack(padx=16, pady=18)
        def ok():
            u = v.get().strip()
            if u:
                self.url_list.insert("end", u)
            d.destroy()
        ttk.Button(d, text="确定", command=ok, width=10).pack()
        d.grab_set()

    def _del_url(self):
        sel = self.url_list.curselection()
        if sel:
            self.url_list.delete(sel[0])

    def _reset_urls(self):
        from core import DEFAULT_MANIFEST_URLS
        self.url_list.delete(0, "end")
        for u in DEFAULT_MANIFEST_URLS:
            self.url_list.insert("end", u)

    def _set_autostart(self, enabled: bool) -> bool:
        if IS_WIN:
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_SET_VALUE)
                if enabled:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{os.path.abspath(sys.executable)}"'
                    else:
                        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                        script = os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))
                        cmd = f'"{pyw}" "{script}"' if os.path.isfile(pyw) else \
                              f'"{sys.executable}" "{script}"'
                    winreg.SetValueEx(key, APP_EN, 0, winreg.REG_SZ, cmd)
                else:
                    try:
                        winreg.DeleteValue(key, APP_EN)
                    except FileNotFoundError:
                        pass
                winreg.CloseKey(key)
                return True
            except Exception as exc:
                self.log("错误", f"设置开机自启失败: {exc}")
                return False
        return True

    def _save(self):
        s = self.config_mgr.settings
        try:
            s["poll_interval_ms"] = max(200, min(10000, int(self.v_poll.get())))
        except ValueError:
            s["poll_interval_ms"] = 800
        s["minimize_to_tray"] = self.v_tray.get()
        s["show_notification"] = self.v_notify.get()
        s["sound_notify"] = self.v_sound.get()
        s["manifest_urls"] = [self.url_list.get(i) for i in range(self.url_list.size())]
        if self.v_start.get() != bool(s.get("start_with_windows", False)):
            if not self._set_autostart(self.v_start.get()):
                return
            s["start_with_windows"] = self.v_start.get()
        self.config_mgr.save()
        self.log("信息", "设置已保存")
        self.destroy()


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME}  {APP_TAGLINE}")
        self.root.geometry("1080x720")
        self.root.minsize(940, 620)
        self.root.configure(bg=BG)
        self._set_window_icon()

        self.config = ConfigManager()
        self.events: queue.Queue = queue.Queue()
        self.tray: Optional[Tray] = None
        self.update_manifest: Optional[dict] = None
        self.downloader = None
        self._log_lines = 0

        from core import MonitorEngine
        self.engine = MonitorEngine(
            self.config,
            on_log=lambda lv, msg: self.events.put(("log", lv, msg)),
            on_task_fired=lambda t: self.events.put(("fired", t)),
            on_notify=lambda title, msg: self.tray.notify(title, msg) if self.tray else None)

        self._build_ui()
        self._refresh_tasks()
        self.engine.apply_tasks(self.config.tasks, prime=True)
        self.engine.start()
        self.tray = Tray(self)

        self.root.after(120, self._drain)
        self.root.after(1500, lambda: self.check_update(silent=True))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_window_icon(self):
        try:
            img = make_icon_image()
            if img:
                ico = os.path.join(os.environ.get("TEMP", "."), APP_EN + ".ico")
                img.save(ico)
                self.root.iconbitmap(ico)
        except Exception:
            pass

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        # 抬头
        head = tk.Frame(self.root, bg=PANEL, height=64)
        head.pack(fill="x", side="top")
        head.pack_propagate(False)
        tk.Label(head, text="喵伴启", font=_font(16, True), bg=PANEL,
                 fg=ACCENT_DARK).pack(side="left", padx=(18, 6))
        tk.Label(head, text=APP_TAGLINE, font=_font(9), bg=PANEL,
                 fg=MUTED).pack(side="left")
        tk.Label(head, text=f"v{APP_VERSION}", font=_font(9), bg=PANEL,
                 fg=MUTED).pack(side="left", padx=8)

        self.v_status = tk.StringVar(value="● 监控已暂停" if self.engine.paused else "● 监控中")
        self.lbl_status = tk.Label(head, textvariable=self.v_status, font=_font(10, True),
                                   bg=PANEL, fg=MUTED if self.engine.paused else OK)
        self.lbl_status.pack(side="right", padx=10)
        ttk.Button(head, text="检查更新", width=10,
                   command=lambda: self.check_update(False)).pack(side="right", padx=6, pady=14)
        self.btn_pause = ttk.Button(head, text="继续监控" if self.engine.paused else "暂停监控",
                                    width=10, command=self.toggle_pause)
        self.btn_pause.pack(side="right", padx=4, pady=14)
        ttk.Button(head, text="设置", width=7, command=self.open_settings).pack(
            side="right", padx=4, pady=14)

        # 非模态更新横幅
        self.update_bar = tk.Frame(self.root, bg="#FFF6E8", height=48)
        self.bar_text = tk.Label(self.update_bar, text="", bg="#FFF6E8", fg=TEXT,
                                 font=_font(10), anchor="w")
        self.bar_text.pack(side="left", fill="x", expand=True, padx=14)
        self.bar_btn = ttk.Button(self.update_bar, text="立即更新", width=10,
                                  command=self._update_now)
        self.bar_btn.pack(side="right", padx=4)
        self.bar_skip = ttk.Button(self.update_bar, text="跳过此版本", width=11,
                                   command=self._skip_update)
        self.bar_skip.pack(side="right", padx=2)
        self.bar_later = ttk.Button(self.update_bar, text="稍后再说", width=10,
                                    command=self._hide_update_bar)
        self.bar_later.pack(side="right", padx=2)
        self.bar_progress = ttk.Progressbar(self.update_bar, mode="determinate", length=140)
        self.bar_progress.pack(side="right", padx=8)

        # 工具栏
        bar = tk.Frame(self.root, bg=BG)
        self.toolbar = bar
        bar.pack(fill="x", padx=14, pady=(12, 6))
        for txt, cmd, w in [
            ("新建任务", self.new_task, 10), ("编辑", self.edit_task, 8),
            ("删除", self.delete_task, 8), ("启用/停用", self.toggle_task, 10),
            ("测试触发", self.test_task, 9), ("导入", self.import_config, 7),
            ("导出", self.export_config, 7),
        ]:
            ttk.Button(bar, text=txt, command=cmd, width=w).pack(side="left", padx=(0, 6))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        tk.Label(bar, text="双击任务可编辑", fg=MUTED, bg=BG, font=_font(9)).pack(side="left")

        # 主体：任务表 + 日志
        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=14, pady=(4, 6))

        left = ttk.Frame(main)
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        cols = ("state", "name", "trigger", "actions", "freq", "count", "last")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=16)
        heads = {
            "state": ("状态", 62), "name": ("任务名称", 200), "trigger": ("触发条件", 190),
            "actions": ("执行动作", 150), "freq": ("频率", 110),
            "count": ("次数", 55), "last": ("上次触发", 130),
        }
        for c in cols:
            self.tree.heading(c, text=heads[c][0])
            self.tree.column(c, width=heads[c][1], anchor="w")
        self.tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(left, command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.config(yscrollcommand=sb.set)
        self.tree.bind("<Double-Button-1>", lambda _: self.edit_task())
        self.tree.bind("<Button-3>", self._task_context_menu)
        self.tree.tag_configure("off", foreground="#9CA3AF")

        tk.Label(right, text="运行日志", font=_font(10, True), bg=BG, fg=TEXT).pack(anchor="w")
        logframe = ttk.Frame(right)
        logframe.pack(fill="both", expand=True, pady=(4, 0))
        self.logbox = tk.Text(logframe, font=_font(9), wrap="word", bg="#FFFFFF",
                              fg=TEXT, relief="flat", padx=8, pady=6)
        self.logbox.pack(side="left", fill="both", expand=True)
        lsb = ttk.Scrollbar(logframe, command=self.logbox.yview)
        lsb.pack(side="right", fill="y")
        self.logbox.config(yscrollcommand=lsb.set)
        for tag, color in [("信息", MUTED), ("触发", ACCENT_DARK), ("完成", OK),
                           ("警告", WARN), ("错误", ERR), ("跳过", "#8B5CF6"),
                           ("测试", "#2563EB"), ("调试", "#B0B7C3"), ("提示", MUTED)]:
            self.logbox.tag_configure(tag, foreground=color)
        ttk.Button(right, text="清空日志", command=lambda: self.logbox.delete("1.0", "end")).pack(
            anchor="e", pady=6)

        # 状态栏
        self.v_foot = tk.StringVar()
        tk.Label(self.root, textvariable=self.v_foot, bg=BG, fg=MUTED,
                 font=_font(9), anchor="w").pack(fill="x", padx=16, pady=(0, 10))
        self._update_foot()

    def _update_foot(self):
        n = len(self.config.tasks)
        on = sum(1 for t in self.config.tasks if t.enabled)
        self.v_foot.set(f"任务 {n} 个（启用 {on}）   |   轮询 {self.config.settings.get('poll_interval_ms', 800)}ms"
                        f"   |   配置 {CONFIG_PATH}   |   关闭窗口可最小化到托盘常驻")

    # ---------- 事件泵 ----------
    def _drain(self):
        try:
            for _ in range(200):
                ev = self.events.get_nowait()
                if ev[0] == "log":
                    self._write_log(ev[1], ev[2])
                elif ev[0] == "fired":
                    self._refresh_tasks()
        except queue.Empty:
            pass
        self.root.after(150, self._drain)

    def _write_log(self, level: str, msg: str):
        self.logbox.insert("end", f"[{now_str()}] ", "信息")
        self.logbox.insert("end", f"{level}  ", level if level in
                           ("信息", "触发", "完成", "警告", "错误", "跳过", "测试", "调试", "提示") else "信息")
        self.logbox.insert("end", msg + "\n")
        self._log_lines += 1
        if self._log_lines > 1200:
            self.logbox.delete("1.0", "100.0")
            self._log_lines = 100
        self.logbox.see("end")

    def log(self, level, msg):
        self.events.put(("log", level, msg))

    # ---------- 任务操作 ----------
    def _selected_task(self) -> Optional[Task]:
        sel = self.tree.selection()
        if not sel:
            return None
        i = self.tree.index(sel[0])
        return self.config.tasks[i] if 0 <= i < len(self.config.tasks) else None

    def _refresh_tasks(self):
        self.tree.delete(*self.tree.get_children())
        for t in self.config.tasks:
            tag = () if t.enabled else ("off",)
            self.tree.insert("", "end", values=(
                "启用" if t.enabled else "停用", t.name, t.trigger.summary(),
                t.actions_summary(), FREQUENCY_MODES.get(t.conditions.frequency, ""),
                t.fire_count, t.last_fired or "—"), tags=tag)
        self._update_foot()

    def _task_context_menu(self, event):
        i = self.tree.identify_row(event.y)
        if i:
            self.tree.selection_set(i)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="编辑", command=self.edit_task)
        menu.add_command(label="测试触发", command=self.test_task)
        menu.add_command(label="启用/停用", command=self.toggle_task)
        menu.add_command(label="删除", command=self.delete_task)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def new_task(self):
        ed = TaskEditor(self.root, None, test_cb=self.engine.test_task)
        if ed.result:
            self.config.tasks.append(ed.result)
            self.config.save()
            self.engine.apply_tasks(self.config.tasks, prime=True)
            self._refresh_tasks()
            self.log("信息", f"已新建任务：{ed.result.name}")

    def edit_task(self):
        t = self._selected_task()
        if not t:
            messagebox.showinfo(APP_NAME, "请先选择一个任务")
            return
        ed = TaskEditor(self.root, t, test_cb=self.engine.test_task)
        if ed.result:
            idx = self.config.tasks.index(t)
            self.config.tasks[idx] = ed.result
            self.config.save()
            self.engine.apply_tasks(self.config.tasks, prime=True)
            self._refresh_tasks()
            self.log("信息", f"已更新任务：{ed.result.name}")

    def delete_task(self):
        t = self._selected_task()
        if not t:
            return
        if messagebox.askyesno(APP_NAME, f"确定删除任务「{t.name}」？"):
            self.config.tasks.remove(t)
            self.config.save()
            self.engine.apply_tasks(self.config.tasks, prime=True)
            self._refresh_tasks()
            self.log("信息", f"已删除任务：{t.name}")

    def toggle_task(self):
        t = self._selected_task()
        if not t:
            return
        t.enabled = not t.enabled
        self.config.save()
        self.engine.apply_tasks(self.config.tasks, prime=True)
        self._refresh_tasks()

    def test_task(self):
        t = self._selected_task()
        if not t:
            return
        self.engine.test_task(t)

    def import_config(self):
        p = filedialog.askopenfilename(filetypes=[("JSON 配置", "*.json"), ("所有文件", "*.*")])
        if not p:
            return
        merge = messagebox.askyesno(APP_NAME, "选择「是」合并导入，选择「否」覆盖现有全部任务")
        n = self.config.import_from(p, merge=merge)
        if n < 0:
            messagebox.showerror(APP_NAME, "配置文件读取失败")
            return
        self.engine.apply_tasks(self.config.tasks, prime=True)
        self._refresh_tasks()
        messagebox.showinfo(APP_NAME, f"已导入 {n} 个任务")

    def export_config(self):
        p = filedialog.asksaveasfilename(defaultextension=".json",
                                         initialfile="喵伴启任务配置.json",
                                         filetypes=[("JSON 配置", "*.json")])
        if p and self.config.export_to(p):
            messagebox.showinfo(APP_NAME, "配置已导出")
        elif p:
            messagebox.showerror(APP_NAME, "导出失败")

    def open_settings(self):
        old = self.config.settings.get("poll_interval_ms", 800)
        SettingsDialog(self.root, self.config, self.log)
        if old != self.config.settings.get("poll_interval_ms"):
            self._update_foot()

    def toggle_pause(self):
        self.engine.set_paused(not self.engine.paused)
        paused = self.engine.paused
        self.v_status.set("● 监控已暂停" if paused else "● 监控中")
        self.lbl_status.config(fg=MUTED if paused else OK)
        self.btn_pause.config(text="继续监控" if paused else "暂停监控")
        self.config.save()

    # ---------- 更新 ----------
    def check_update(self, silent=False):
        self.log("信息", "正在检查更新...")
        urls = self.config.settings.get("manifest_urls", [])
        skipped = self.config.settings.get("skipped_version", "")
        def work():
            mf = updater.check_updates(urls, self.log, skipped)
            self.root.after(0, lambda: self._on_check_done(mf, silent))
        threading.Thread(target=work, daemon=True).start()

    def _on_check_done(self, manifest, silent):
        if not manifest:
            if not silent:
                messagebox.showinfo(APP_NAME, f"当前已是最新版本 v{APP_VERSION}")
            return
        self.update_manifest = manifest
        self._show_update_bar(f"发现新版本 v{manifest.get('version')}"
                              f"　{str(manifest.get('notes', ''))[:60]}", ready=True)

    def _show_update_bar(self, text, ready=False, progress=None):
        self.update_bar.pack(fill="x", side="top", before=self.toolbar)
        self.bar_text.config(text=text)
        if ready:
            self.bar_btn.config(text="立即更新", command=self._update_now, state="normal")
            self.bar_skip.pack(side="right", padx=2)
            self.bar_later.config(text="稍后再说", command=self._hide_update_bar)
            self.bar_later.pack(side="right", padx=2)
            self.bar_progress.pack_forget()
        else:
            self.bar_skip.pack_forget()
            self.bar_later.config(text="取消", command=self._cancel_update)
            self.bar_later.pack(side="right", padx=2)
            self.bar_btn.config(state="disabled")
            self.bar_progress.pack(side="right", padx=8)
        if progress is not None:
            ratio, msg = progress
            self.bar_progress["value"] = ratio * 100
            if msg:
                self.bar_text.config(text=msg)

    def _hide_update_bar(self):
        self.update_bar.pack_forget()

    def _skip_update(self):
        if self.update_manifest:
            self.config.settings["skipped_version"] = str(self.update_manifest.get("version"))
            self.config.save()
        self._hide_update_bar()

    def _cancel_update(self):
        if self.downloader:
            self.downloader.cancel()
        self._hide_update_bar()

    def _update_now(self):
        if not self.update_manifest:
            return
        self._show_update_bar("准备下载更新...", ready=False)
        mf = self.update_manifest
        def finished(path, err):
            self.root.after(0, lambda: self._on_download_done(path, err))
        self.downloader = updater.UpdateDownloader(
            mf,
            progress=lambda r, m: self.root.after(0, lambda: self._show_update_bar(
                "", ready=False, progress=(r, m))),
            log=self.log,
            finished=finished)
        self.downloader.start()

    def _on_download_done(self, path, err):
        self.downloader = None
        if not path:
            self._show_update_bar(f"更新失败：{err}　", ready=True)
            self.bar_btn.config(text="重试更新", command=self._update_now)
            self.bar_skip.pack_forget()
            return
        ok, msg = updater.install_update(path, self.log)
        if not ok:
            self._show_update_bar(f"安装失败：{msg}", ready=True)
            return
        ver = self.update_manifest.get("version", "新版")
        self._hide_update_bar()
        if messagebox.askyesno(APP_NAME,
                               f"v{ver} 已安装完成。\n\n是否立即重启软件？\n"
                               f"选择“否”则下次打开时生效。"):
            updater.restart_app()
            self.root.after(3000, lambda: os._exit(0))

    # ---------- 生命周期 ----------
    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(400, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def on_close(self):
        if self.config.settings.get("minimize_to_tray", True) and self.tray and self.tray.icon:
            self.root.withdraw()
            if self.config.settings.get("show_notification", True):
                self.tray.notify(APP_NAME, "已最小化到托盘，仍在后台监控软件启动")
        else:
            self.quit_app()

    def quit_app(self):
        try:
            self.engine.stop()
            self.config.save()
        except Exception:
            pass
        if self.tray:
            self.tray.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = App(root)
    root.mainloop()
