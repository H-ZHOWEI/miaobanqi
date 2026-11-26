# -*- coding: utf-8 -*-
"""
喵伴启 - 核心层
数据模型 / 配置持久化 / 进程探测 / 监控引擎
"""
from __future__ import annotations

import fnmatch
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

IS_WIN = sys.platform == "win32"

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_NAME = "喵伴启"
APP_EN = "MiaoBanQi"
APP_VERSION = "1.0.3"
APP_TAGLINE = "智能软件联动启动器"

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".miaobanqi")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")

DEFAULT_MANIFEST_URLS = [
    "https://raw.githubusercontent.com/H-ZHOWEI/miaobanqi/main/version.json",
    "https://ghproxy.net/https://raw.githubusercontent.com/H-ZHOWEI/miaobanqi/main/version.json",
    "https://gh-proxy.com/https://raw.githubusercontent.com/H-ZHOWEI/miaobanqi/main/version.json",
]

TRIGGER_FIELDS = {
    "name": "进程名",
    "path": "进程路径",
    "title": "窗口标题",
}
MATCH_MODES = {
    "exact": "精确匹配",
    "contains": "包含",
    "wildcard": "通配符(* ?)",
}
FREQUENCY_MODES = {
    "on_close": "A每次从关闭到重新打开时触发（推荐）",
    "every_new_process": "冷启动后每个新进程都触发（高级）",
    "interval": "A运行中定时重复（非首次打开）",
    "once_per_day": "每天首次打开A时触发",
    "once_ever": "一生只触发一次",
}
ACTION_TYPES = {
    "launch": "启动程序",
    "open": "打开文件/网址",
    "delay": "延时等待",
    "kill": "结束进程",
    "notify": "弹出提醒",
}
WEEKDAY_LABELS = ["一", "二", "三", "四", "五", "六", "日"]

# 窗口显示模式：内部值 -> 中文显示
SHOW_MODES = {
    "normal": "正常显示",
    "hidden": "隐藏窗口（后台运行）",
    "minimized": "最小化",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def stamp_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_version(v: str) -> tuple:
    parts = []
    for p in str(v).lstrip("vV").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def version_gt(a: str, b: str) -> bool:
    return parse_version(a) > parse_version(b)


def safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join(("_" if c in bad else c) for c in name).strip() or "未命名"


def match_value(value: str, pattern: str, mode: str = "contains",
                case_sensitive: bool = False) -> bool:
    if not value or not pattern:
        return False
    v, p = value, pattern
    if not case_sensitive:
        v, p = v.lower(), p.lower()
    if mode == "exact":
        return v == p
    if mode == "wildcard":
        try:
            return fnmatch.fnmatchcase(v, p)
        except Exception:
            return False
    return p in v


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------
@dataclass
class Trigger:
    field: str = "name"            # name / path / title
    value: str = ""
    match: str = "contains"        # exact / contains / wildcard
    title_filter: str = ""         # 附加条件：窗口标题需包含
    case_sensitive: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Trigger":
        d = d or {}
        return Trigger(
            field=d.get("field", "name") if d.get("field") in TRIGGER_FIELDS else "name",
            value=str(d.get("value", "")),
            match=d.get("match", "contains") if d.get("match") in MATCH_MODES else "contains",
            title_filter=str(d.get("title_filter", "")),
            case_sensitive=bool(d.get("case_sensitive", False)),
        )

    def summary(self) -> str:
        if not self.value:
            return "未设置"
        label = TRIGGER_FIELDS.get(self.field, self.field)
        mode = "" if self.match == "contains" else f"[{MATCH_MODES.get(self.match, '')}]"
        return f"{label}: {mode}{self.value}"


@dataclass
class Conditions:
    require_not_running: str = ""   # 这些进程运行时不触发（逗号分隔）
    require_running: str = ""       # 仅当这些进程运行时触发（逗号分隔）
    time_start: str = ""            # HH:MM，空为不限
    time_end: str = ""
    weekdays: List[int] = field(default_factory=list)  # 0=周一, 空=每天
    frequency: str = "on_close"
    interval_sec: int = 60
    debounce_ms: int = 400
    pre_delay_ms: int = 0
    cooldown_sec: int = 2
    fire_if_already_running: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Conditions":
        d = d or {}
        wd = d.get("weekdays", [])
        if not isinstance(wd, list):
            wd = []
        wd = [int(x) for x in wd if str(x).isdigit() and 0 <= int(x) <= 6]
        freq = d.get("frequency", "on_close")
        if freq not in FREQUENCY_MODES:
            freq = "on_close"
        def _int(key, default, lo, hi):
            try:
                return max(lo, min(hi, int(d.get(key, default))))
            except (TypeError, ValueError):
                return default
        return Conditions(
            require_not_running=str(d.get("require_not_running", "")),
            require_running=str(d.get("require_running", "")),
            time_start=str(d.get("time_start", "")),
            time_end=str(d.get("time_end", "")),
            weekdays=sorted(set(wd)),
            frequency=freq,
            interval_sec=_int("interval_sec", 60, 1, 86400),
            debounce_ms=_int("debounce_ms", 400, 0, 30000),
            pre_delay_ms=_int("pre_delay_ms", 0, 0, 300000),
            cooldown_sec=_int("cooldown_sec", 2, 0, 86400),
            fire_if_already_running=bool(d.get("fire_if_already_running", False)),
        )


@dataclass
class Action:
    type: str = "launch"           # launch / open / delay / kill / notify
    path: str = ""                 # launch 目标
    args: str = ""                 # launch 参数
    cwd: str = ""                  # 工作目录
    admin: bool = False            # 管理员运行
    show: str = "normal"           # normal / hidden / minimized
    value: str = ""                # open 的路径/网址, notify 的文本, kill 的进程名
    delay_ms: int = 1000           # delay 的时长
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: Optional[dict]) -> "Action":
        d = d or {}
        t = d.get("type", "launch")
        if t not in ACTION_TYPES:
            t = "launch"
        try:
            delay = max(0, min(3600000, int(d.get("delay_ms", 1000))))
        except (TypeError, ValueError):
            delay = 1000
        show = d.get("show", "normal")
        if show not in ("normal", "hidden", "minimized"):
            show = "normal"
        return Action(
            type=t,
            path=str(d.get("path", "")),
            args=str(d.get("args", "")),
            cwd=str(d.get("cwd", "")),
            admin=bool(d.get("admin", False)),
            show=show,
            value=str(d.get("value", "")),
            delay_ms=delay,
            note=str(d.get("note", "")),
        )

    def summary(self) -> str:
        label = ACTION_TYPES.get(self.type, self.type)
        if self.type == "launch":
            return f"{label}: {os.path.basename(self.path) or self.path}"
        if self.type == "delay":
            return f"{label}: {self.delay_ms}ms"
        if self.type == "kill":
            return f"{label}: {self.value}"
        if self.type == "notify":
            return f"{label}: {self.value[:20]}"
        return f"{label}: {self.value[:30]}"


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = "新建任务"
    enabled: bool = True
    note: str = ""
    trigger: Trigger = field(default_factory=Trigger)
    conditions: Conditions = field(default_factory=Conditions)
    actions: List[Action] = field(default_factory=list)
    # 运行态统计（持久化，便于查看）
    fire_count: int = 0
    last_fired: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "note": self.note,
            "trigger": self.trigger.to_dict(),
            "conditions": self.conditions.to_dict(),
            "actions": [a.to_dict() for a in self.actions],
            "fire_count": self.fire_count,
            "last_fired": self.last_fired,
        }

    @staticmethod
    def from_dict(d: dict) -> "Task":
        d = d or {}
        actions = d.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        return Task(
            id=str(d.get("id") or uuid.uuid4().hex[:12]),
            name=str(d.get("name") or "新建任务"),
            enabled=bool(d.get("enabled", True)),
            note=str(d.get("note", "")),
            trigger=Trigger.from_dict(d.get("trigger")),
            conditions=Conditions.from_dict(d.get("conditions")),
            actions=[Action.from_dict(a) for a in actions],
            fire_count=int(d.get("fire_count", 0) or 0),
            last_fired=str(d.get("last_fired", "")),
        )

    def actions_summary(self) -> str:
        if not self.actions:
            return "未设置"
        names = [ACTION_TYPES.get(a.type, a.type) for a in self.actions]
        seen = []
        for n in names:
            if n not in seen:
                seen.append(n)
        text = " → ".join(seen)
        return text if len(self.actions) <= 3 else f"{text} 等{len(self.actions)}项"

    def validate(self) -> List[str]:
        errs = []
        if not self.name.strip():
            errs.append("任务名称不能为空")
        if not self.trigger.value.strip():
            errs.append("触发条件不能为空")
        if not self.actions:
            errs.append("至少需要一个执行动作")
        for i, a in enumerate(self.actions, 1):
            if a.type in ("launch", "open") and not (a.path if a.type == "launch" else a.value).strip():
                errs.append(f"第{i}个动作缺少目标")
            if a.type == "kill" and not a.value.strip():
                errs.append(f"第{i}个动作缺少进程名")
        return errs


DEFAULT_SETTINGS: Dict[str, Any] = {
    "poll_interval_ms": 800,
    "minimize_to_tray": True,
    "start_with_windows": False,
    "show_notification": True,
    "sound_notify": False,
    "log_retention_days": 30,
    "manifest_urls": list(DEFAULT_MANIFEST_URLS),
    "skipped_version": "",
    "last_check": "",
    "monitor_paused": False,
}


class ConfigManager:
    """配置读写：原子写入 + 损坏备份 + 导入导出"""

    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.tasks: List[Task] = []
        self.settings: Dict[str, Any] = dict(DEFAULT_SETTINGS)
        self.lock = threading.RLock()
        self.load()
        # 防御：任何情况下都保证 tasks 里只有 Task 对象
        self.tasks = [t for t in self.tasks if isinstance(t, Task)]

    def load(self) -> None:
        with self.lock:
            if not os.path.isfile(self.path):
                self.tasks = self._demo_task()
                return
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                # 损坏文件备份，不覆盖原始数据
                bak = self.path + ".broken." + datetime.now().strftime("%Y%m%d%H%M%S")
                try:
                    os.replace(self.path, bak)
                except Exception:
                    pass
                self.tasks = self._demo_task()
                self.last_error = f"配置读取失败，已备份到 {bak}: {exc}"
                return
            raw_tasks = data.get("tasks", [])
            self.tasks = [Task.from_dict(t) for t in raw_tasks if isinstance(t, dict)]
            st = dict(DEFAULT_SETTINGS)
            st.update({k: v for k, v in (data.get("settings") or {}).items() if k in DEFAULT_SETTINGS})
            self.settings = st

    def save(self) -> bool:
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                data = {
                    "app": APP_EN,
                    "version": APP_VERSION,
                    "saved_at": stamp_str(),
                    "settings": self.settings,
                    "tasks": [t.to_dict() for t in self.tasks],
                }
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
                return True
            except Exception:
                return False

    def export_to(self, target: str) -> bool:
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump({
                    "app": APP_EN,
                    "exported_at": stamp_str(),
                    "settings": self.settings,
                    "tasks": [t.to_dict() for t in self.tasks],
                }, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def import_from(self, source: str, merge: bool = True) -> int:
        with self.lock:
            try:
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                return -1
            incoming = [Task.from_dict(t) for t in (data.get("tasks") or []) if isinstance(t, dict)]
            if not incoming:
                return 0
            if merge:
                known = {t.id for t in self.tasks}
                for t in incoming:
                    if t.id not in known:
                        self.tasks.append(t)
                    else:
                        # 同 ID 覆盖
                        self.tasks = [t if x.id != t.id else t for x in self.tasks]
            else:
                self.tasks = incoming
            if isinstance(data.get("settings"), dict):
                for k, v in data["settings"].items():
                    if k in DEFAULT_SETTINGS and k not in ("manifest_urls", "skipped_version"):
                        self.settings[k] = v
            self.save()
            return len(incoming)

    @staticmethod
    def _demo_task() -> List["Task"]:
        """首次运行的示例任务：打开记事本时自动启动计算器"""
        return [Task(
            name="示例：打开记事本时启动计算器",
            note="这是一个示例任务，熟悉后可删除或改成你自己的软件。",
            enabled=False,
            trigger=Trigger(field="name", value="notepad.exe", match="exact"),
            conditions=Conditions(frequency="on_close", debounce_ms=500, cooldown_sec=3),
            actions=[
                Action(type="launch", path="calc.exe", note="Windows 自带计算器"),
                Action(type="notify", value="记事本已打开，计算器已为你启动"),
            ],
        )]


# ---------------------------------------------------------------------------
# 进程探测
# ---------------------------------------------------------------------------
if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def window_titles() -> Dict[int, List[str]]:
        """枚举可见窗口标题，按进程 ID 归组（纯 ctypes，无额外依赖）"""
        result: Dict[int, List[str]] = {}

        def _cb(hwnd, _lparam):
            try:
                if _user32.IsWindowVisible(hwnd):
                    length = _user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 2)
                        _user32.GetWindowTextW(hwnd, buf, length + 2)
                        title = buf.value.strip()
                        if title:
                            pid = wintypes.DWORD()
                            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            if pid.value:
                                result.setdefault(pid.value, []).append(title)
            except Exception:
                pass
            return True

        try:
            _user32.EnumWindows(_ENUM_PROC(_cb), 0)
        except Exception:
            pass
        return result
else:  # pragma: no cover
    def window_titles() -> Dict[int, List[str]]:
        return {}


def process_snapshot() -> Dict[int, Dict[str, Any]]:
    """返回当前进程快照：pid -> {name, exe, create_time}"""
    result: Dict[int, Dict[str, Any]] = {}
    if psutil is None:
        return result
    for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            info = p.info
            result[int(info["pid"])] = {
                "name": info.get("name") or "",
                "exe": info.get("exe") or "",
                "create_time": info.get("create_time") or 0,
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        except Exception:
            continue
    return result


def process_list() -> List[Dict[str, Any]]:
    """供"选择运行中的程序"使用：返回进程名/PID/窗口标题/内存/路径等详情"""
    titles = window_titles()
    rows: List[Dict[str, Any]] = []
    if psutil is None:
        return rows
    for p in psutil.process_iter(["pid", "name", "exe", "username", "memory_info"], ad_value=None):
        try:
            info = p.info
            pid = int(info.get("pid") or 0)
            if not pid:
                continue
            wtitles = titles.get(pid, [])
            mem = info.get("memory_info")
            rows.append({
                "pid": pid,
                "name": info.get("name") or "",
                "exe": info.get("exe") or "",
                "user": info.get("username") or "",
                "mem": round((mem.rss / 1024 / 1024), 1) if mem else 0.0,
                "titles": wtitles,
                "title": wtitles[0] if wtitles else "",
            })
        except Exception:
            continue
    rows.sort(key=lambda r: (r["name"].lower(), r["pid"]))
    return rows


def name_running(names: str) -> bool:
    """逗号分隔的进程名中是否有一个正在运行（包含匹配，忽略大小写）"""
    targets = [x.strip().lower() for x in names.split(",") if x.strip()]
    if not targets:
        return False
    for info in process_snapshot().values():
        n = (info.get("name") or "").lower()
        if any(t in n for t in targets):
            return True
    return False


def run_action(action: Action, log: Callable[[str, str], None]) -> bool:
    """执行单个动作，返回是否成功"""
    try:
        if action.type == "delay":
            time.sleep(action.delay_ms / 1000.0)
            return True

        if action.type == "launch":
            target = os.path.expandvars(os.path.expanduser(action.path.strip()))
            if not target:
                log("错误", "启动路径为空")
                return False
            args = action.args.strip()
            argv = [target]
            if args:
                try:
                    argv.extend(shlex.split(args, posix=False))
                except ValueError:
                    argv.extend(args.split())
            cwd = os.path.expandvars(os.path.expanduser(action.cwd.strip())) or None
            if cwd and not os.path.isdir(cwd):
                cwd = None

            if action.admin and IS_WIN:
                show = {"normal": 1, "hidden": 0, "minimized": 6}.get(action.show, 1)
                rc = ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", target, args, cwd or "", show)
                ok = int(rc) > 32
                log("信息" if ok else "错误",
                    f"{'已请求管理员启动' if ok else '管理员启动失败(rc=' + str(rc) + ')'}: {target}")
                return ok

            flags = 0
            if IS_WIN:
                if action.show == "hidden":
                    flags |= subprocess.CREATE_NO_WINDOW
                elif action.show == "minimized":
                    flags |= subprocess.CREATE_NEW_PROCESS_GROUP
            startup = None
            if IS_WIN and action.show == "minimized":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 6  # SW_MINIMIZE
            subprocess.Popen(argv, cwd=cwd, creationflags=flags,
                             startupinfo=startup,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             close_fds=True)
            log("信息", f"已启动: {target}" + (f" {args}" if args else ""))
            return True

        if action.type == "open":
            value = os.path.expandvars(os.path.expanduser(action.value.strip()))
            if not value:
                log("错误", "打开目标为空")
                return False
            if IS_WIN:
                os.startfile(value)  # type: ignore[attr-defined]
            else:  # pragma: no cover
                subprocess.Popen(["xdg-open", value])
            log("信息", f"已打开: {value}")
            return True

        if action.type == "kill":
            targets = [x.strip().lower() for x in action.value.split(",") if x.strip()]
            killed = 0
            for proc in psutil.process_iter(["pid", "name"]) if psutil else []:
                try:
                    n = (proc.info.get("name") or "").lower()
                    if any(t in n for t in targets):
                        proc.terminate()
                        killed += 1
                except Exception:
                    pass
            log("信息" if killed else "警告",
                f"结束进程 {action.value}: 已结束 {killed} 个")
            return killed > 0

        if action.type == "notify":
            log("信息", f"提醒: {action.value}")
            return True

    except FileNotFoundError:
        log("错误", f"找不到目标: {action.path or action.value}")
    except Exception as exc:
        log("错误", f"动作执行失败({ACTION_TYPES.get(action.type, action.type)}): {exc}")
    return False


# ---------------------------------------------------------------------------
# 监控引擎
# ---------------------------------------------------------------------------
class MonitorEngine(threading.Thread):
    """
    后台轮询监控。
    状态机：armed(待触发) -> pending(防抖中) -> fired(已触发)
    仅在触发进程完全消失后重新 armed（on_close 模式）。
    """

    def __init__(self,
                 config: ConfigManager,
                 on_log: Callable[[str, str], None],
                 on_task_fired: Optional[Callable[[Task], None]] = None,
                 on_notify: Optional[Callable[[str, str], None]] = None):
        super().__init__(name="MiaoBanQi-Monitor", daemon=True)
        self.config = config
        self.on_log = on_log
        self.on_task_fired = on_task_fired
        self.on_notify = on_notify
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state: Dict[str, dict] = {}
        self._recent_launched: Dict[str, float] = {}
        self._tasks_snapshot: List[Task] = []
        self._action_pool: List[threading.Thread] = []
        self.paused = bool(config.settings.get("monitor_paused", False))
        self._prime_on_start = True

    # ---- 生命周期 ----
    def apply_tasks(self, tasks: List[Task], prime: bool = False) -> None:
        self._tasks_snapshot = list(tasks)
        if prime:
            self._prime_on_start = True
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.config.settings["monitor_paused"] = paused
        self.on_log("信息", "监控已暂停" if paused else "监控已继续")
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def run(self) -> None:
        self.on_log("信息", "监控引擎已启动")
        while not self._stop.is_set():
            interval = max(0.2, float(self.config.settings.get("poll_interval_ms", 800)) / 1000.0)
            self._wake.wait(interval)
            self._wake.clear()
            if self._stop.is_set() or self.paused:
                continue
            try:
                self._tick()
            except Exception as exc:
                self.on_log("错误", f"监控轮询异常: {exc}")

    # ---- 主循环 ----
    def _tick(self) -> None:
        procs = process_snapshot()
        titles = window_titles()
        now = time.time()

        if self._prime_on_start:
            # 启动时已运行的进程一律视为"早就开着"，不得当作一次新的打开。
            # 这是"仅首次打开 A 才触发 B"的基线：已经运行的 A 不算打开。
            for task in self._tasks_snapshot:
                pids = self._match_pids(task, procs, titles)
                st = self._state.setdefault(task.id, self._new_state())
                st["seen_pids"] = set(pids)
                st["observed"] = set(pids)
                st["pending_since"] = 0.0
                st["phase"] = "armed" if not pids else "fired"
                # 启动时目标不存在 = 已经处于"关闭"状态，之后出现才算冷启动
                st["ever_closed"] = not pids
            self._prime_on_start = False

        for task in self._tasks_snapshot:
            if not task.enabled:
                continue
            if not task.trigger.value.strip():
                continue
            st = self._state.setdefault(task.id, self._new_state())
            pids = self._match_pids(task, procs, titles)
            pids_set = set(pids)

            freq = task.conditions.frequency
            should_fire, reason = self._decide(task, st, pids_set, now, procs)

            # 清理已消失进程的"见过"记录，避免 PID 复用误判
            st["seen_pids"] &= pids_set
            st["observed"] &= pids_set
            if not pids_set:
                # 目标彻底消失 = 一次完整"关闭"，下次再出现才算冷启动
                st["ever_closed"] = True
                st["phase"] = "armed"
                st["pending_since"] = 0

            if not should_fire:
                continue
            if not self._conditions_ok(task, procs):
                continue

            st["last_fire"] = now
            task.last_fired = stamp_str()
            task.fire_count += 1
            self.on_log("触发", f"[{task.name}] {reason}")
            if self.on_task_fired:
                try:
                    self.on_task_fired(task)
                except Exception:
                    pass
            # 记录自身启动的进程名，防止 A→B→A 无限链
            for a in task.actions:
                if a.type == "launch" and a.path:
                    self._recent_launched[os.path.basename(a.path).lower()] = now

            delay = task.conditions.pre_delay_ms / 1000.0
            if delay > 0:
                t = threading.Timer(delay, self._execute, args=(task,))
                t.daemon = True
                t.start()
                self._action_pool.append(t)
            else:
                self._execute(task)

            self.config.save()

    @staticmethod
    def _new_state() -> dict:
        return {
            "phase": "armed",
            "pending_since": 0.0,
            "last_fire": 0.0,
            "seen_pids": set(),
            "observed": set(),
            # 关键：是否曾观察到目标进程"完全消失"。
            # 只有出现过 有→无→有 的跃变，才算"首次打开 A"。
            # A 一直运行时（含用户再次双击/切回窗口/产生额外 PID）恒为 False。
            "ever_closed": False,
        }

    def _decide(self, task: Task, st: dict, pids: Set[int], now: float,
                procs: Dict[int, dict]) -> tuple:
        """判断本次是否应触发，返回 (bool, 原因)"""
        if not pids:
            return False, ""

        # ===== 冷启动守卫（核心规则，对所有触发频率生效）=====
        # 必须亲眼见过 A 从"完全不存在"变为"出现"，才算一次"打开 A"。
        # A 一直开着时，用户再次双击图标、切回窗口、打开新标签/文档
        # 产生的额外 PID 都不满足此条件，因此绝不会误触发 B。
        if not st.get("ever_closed", False):
            return False, ""

        cond = task.conditions
        # 防重复：软件自己刚启动的进程不再作为触发源（15 秒内）
        for pid in pids:
            nm = (procs.get(pid, {}).get("name") or "").lower()
            if nm and now - self._recent_launched.get(nm, 0) < 15:
                return False, ""

        # 冷却时间
        if cond.cooldown_sec > 0 and now - st["last_fire"] < cond.cooldown_sec:
            return False, ""

        freq = cond.frequency

        if freq == "every_new_process":
            # 同样遵守"仅首次打开"：只有从完全关闭恢复的那一刻算一次打开；
            # A 已运行时产生的新 PID 不再触发 B。
            if st["phase"] != "armed":
                return False, ""
            fresh = pids - st["seen_pids"]
            st["seen_pids"] |= pids
            if not fresh:
                return False, ""
            st["phase"] = "fired"
            st["pending_since"] = 0
            return True, f"检测到新实例 (PID {','.join(str(x) for x in sorted(fresh))})"

        if freq == "once_ever":
            if task.fire_count > 0:
                return False, ""
        elif freq == "once_per_day":
            if task.last_fired.startswith(datetime.now().strftime("%Y-%m-%d")):
                return False, ""

        if freq == "interval":
            if st["phase"] == "fired" and now - st["last_fire"] < cond.interval_sec:
                return False, ""
            if st["phase"] == "armed":
                if not st["pending_since"]:
                    st["pending_since"] = now
                if now - st["pending_since"] < cond.debounce_ms / 1000.0:
                    return False, ""
            st["phase"] = "fired"
            st["pending_since"] = 0
            return True, f"运行中定时触发（每 {cond.interval_sec} 秒）"

        # on_close / once_per_day / once_ever 共用"出现即触发"
        if st["phase"] == "fired":
            return False, ""
        if not st["pending_since"]:
            st["pending_since"] = now
        if now - st["pending_since"] < cond.debounce_ms / 1000.0:
            return False, ""
        st["phase"] = "fired"
        st["pending_since"] = 0
        label = {
            "on_close": "检测到软件启动",
            "once_per_day": "今日首次检测到软件启动",
            "once_ever": "首次检测到软件启动",
        }.get(freq, "检测到软件启动")
        return True, f"{label} (PID {','.join(str(x) for x in sorted(pids))})"

    def _conditions_ok(self, task: Task, procs: Dict[int, dict]) -> bool:
        cond = task.conditions
        dt = datetime.now()

        if cond.weekdays and dt.weekday() not in cond.weekdays:
            return False
        cur = dt.strftime("%H:%M")
        if cond.time_start and cond.time_end:
            if cond.time_start <= cond.time_end:
                if not (cond.time_start <= cur <= cond.time_end):
                    return False
            else:  # 跨午夜
                if not (cur >= cond.time_start or cur <= cond.time_end):
                    return False
        elif cond.time_start and cur < cond.time_start:
            return False
        elif cond.time_end and cur > cond.time_end:
            return False

        if cond.require_not_running and name_running(cond.require_not_running):
            self.on_log("跳过", f"[{task.name}] 条件未满足：{cond.require_not_running} 正在运行")
            return False
        if cond.require_running and not name_running(cond.require_running):
            self.on_log("跳过", f"[{task.name}] 条件未满足：{cond.require_running} 未运行")
            return False
        return True

    def _match_pids(self, task: Task, procs: Dict[int, dict],
                    titles: Dict[int, List[str]]) -> List[int]:
        tr = task.trigger
        field_name = tr.field
        out = []
        for pid, info in procs.items():
            if field_name == "title":
                for t in titles.get(pid, []):
                    if match_value(t, tr.value, tr.match, tr.case_sensitive):
                        out.append(pid)
                        break
                continue
            val = info.get(field_name) or ""
            if not match_value(val, tr.value, tr.match, tr.case_sensitive):
                continue
            if tr.title_filter:
                if not any(match_value(t, tr.title_filter, "contains", tr.case_sensitive)
                           for t in titles.get(pid, [])):
                    continue
            out.append(pid)
        return out

    def _execute(self, task: Task) -> None:
        count = 0
        for action in task.actions:
            if self._stop.is_set():
                break
            run_action(action, self.on_log)
            count += 1
            if action.type == "notify" and self.on_notify:
                try:
                    self.on_notify(task.name, action.value)
                except Exception:
                    pass
        self.on_log("完成", f"[{task.name}] 已执行 {count} 个动作")

    def test_task(self, task: Task) -> None:
        """手动测试：绕过触发条件直接执行动作"""
        self.on_log("测试", f"[{task.name}] 手动测试开始")
        t = threading.Thread(target=self._execute, args=(task,), daemon=True)
        t.start()
