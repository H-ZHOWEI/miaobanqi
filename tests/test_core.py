# -*- coding: utf-8 -*-
"""核心逻辑单元测试： python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import (Action, Conditions, ConfigManager, Task, Trigger, match_value,
                  parse_version, process_list, version_gt)  # noqa: E402


class TestMatch(unittest.TestCase):
    def test_modes(self):
        self.assertTrue(match_value("notepad.exe", "notepad.exe", "exact"))
        self.assertFalse(match_value("notepad.exe", "note", "exact"))
        self.assertTrue(match_value("Code.exe", "code", "contains"))
        self.assertFalse(match_value("Code.exe", "code", "contains", True))
        self.assertTrue(match_value("chrome.exe", "chrome*.exe", "wildcard"))
        self.assertFalse(match_value("firefox.exe", "chrome*", "wildcard"))


class TestVersion(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(parse_version("v1.10.2"), (1, 10, 2, 0))
        self.assertTrue(version_gt("1.10", "1.9"))
        self.assertFalse(version_gt("1.0.0", "1.0.0"))


class TestTaskModel(unittest.TestCase):
    def test_round_trip(self):
        t = Task(
            name="测试任务",
            trigger=Trigger(field="title", value="文档", match="contains"),
            conditions=Conditions(weekdays=[0, 2], frequency="interval", interval_sec=45),
            actions=[Action(type="launch", path="calc.exe"),
                     Action(type="delay", delay_ms=500)],
        )
        d = t.to_dict()
        t2 = Task.from_dict(d)
        self.assertEqual(t2.name, t.name)
        self.assertEqual(t2.trigger.value, "文档")
        self.assertEqual(t2.conditions.weekdays, [0, 2])
        self.assertEqual(t2.conditions.interval_sec, 45)
        self.assertEqual(len(t2.actions), 2)
        self.assertEqual(t2.actions[1].delay_ms, 500)

    def test_validation(self):
        t = Task(name="", trigger=Trigger(), actions=[])
        errs = t.validate()
        self.assertGreaterEqual(len(errs), 3)


class TestConfig(unittest.TestCase):
    def test_first_run_demo_task_type(self):
        """首次运行（无配置文件）时，任务列表必须是 Task 对象列表，不能多包一层"""
        with tempfile.TemporaryDirectory() as d:
            c = ConfigManager(os.path.join(d, "fresh.json"))
            self.assertEqual(len(c.tasks), 1)
            self.assertIsInstance(c.tasks[0], Task)
            # 模拟主界面状态栏统计
            self.assertEqual(sum(1 for t in c.tasks if t.enabled), 0)

    def test_save_load_export_import(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            c = ConfigManager(path)
            c.tasks = [Task(name="A", trigger=Trigger(value="a.exe"),
                            actions=[Action(path="b.exe")])]
            self.assertTrue(c.save())
            c2 = ConfigManager(path)
            self.assertEqual(len(c2.tasks), 1)
            self.assertEqual(c2.tasks[0].name, "A")

            exp = os.path.join(d, "export.json")
            self.assertTrue(c2.export_to(exp))
            c3 = ConfigManager(path)
            c3.tasks = []
            self.assertEqual(c3.import_from(exp, merge=True), 1)
            self.assertEqual(len(c3.tasks), 1)


class TestProcessList(unittest.TestCase):
    def test_running_processes(self):
        rows = process_list()
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 5)
        for r in rows:
            self.assertIn("pid", r)
            self.assertIn("name", r)
            self.assertIn("titles", r)
            self.assertIsInstance(r["titles"], list)


class TestDecide(unittest.TestCase):
    def test_frequency_and_debounce(self):
        from core import MonitorEngine
        logs = []
        eng = MonitorEngine(ConfigManager(os.path.join(tempfile.gettempdir(), "mbq-test.json")),
                            lambda lv, msg: logs.append((lv, msg)))
        t = Task(name="T", trigger=Trigger(value="a"),
                 actions=[Action(path="b.exe")])
        st = MonitorEngine._new_state()
        st["ever_closed"] = True  # 模拟已经历过一次完整关闭，允许冷启动触发
        now = 1000.0
        # 第一次检测：防抖未到
        ok, _ = eng._decide(t, st, {1}, now, {1: {"name": "a.exe"}})
        self.assertFalse(ok)
        # 防抖完成
        ok, _ = eng._decide(t, st, {1}, now + 1, {1: {"name": "a.exe"}})
        self.assertTrue(ok)
        # 同一次运行不再触发
        ok, _ = eng._decide(t, st, {1}, now + 2, {1: {"name": "a.exe"}})
        self.assertFalse(ok)


class TestColdStart(unittest.TestCase):
    """核心规则：只有 A 从完全消失到重新出现才算一次“打开 A”，才触发 B。"""

    def setUp(self):
        from core import MonitorEngine
        self.fired = []
        self.cfg = ConfigManager(os.path.join(tempfile.gettempdir(), "mbq-coldstart-test.json"))
        self.eng = MonitorEngine(
            self.cfg, lambda lv, msg: None,
            on_task_fired=lambda t: self.fired.append(t.name))
        self.eng._execute = lambda t: None  # 不真的执行动作
        self.task = Task(
            name="联动B",
            trigger=Trigger(field="name", value="a.exe", match="exact"),
            conditions=Conditions(frequency="on_close", debounce_ms=0, cooldown_sec=0),
            actions=[Action(type="notify", value="test")])

    def _tick_with(self, pids, titles=None, task=None):
        procs = {pid: {"name": "a.exe", "exe": "C:\\a.exe"} for pid in pids}
        with patch("core.process_snapshot", return_value=procs), \
             patch("core.window_titles", return_value=titles or {}):
            self.eng._tick()

    def test_a_already_running_at_start_no_trigger(self):
        """A 在软件启动前就已运行：无论轮询多少次都不能触发 B"""
        self.eng.apply_tasks([self.task], prime=True)
        for _ in range(5):
            self._tick_with([100])
        self.assertEqual(len(self.fired), 0, "A 已运行时不应触发 B")

    def test_a_cold_start_triggers_once(self):
        """A 从无到有：触发一次"""
        self.eng.apply_tasks([self.task], prime=True)
        self._tick_with([])       # A 不存在 → 记录一次关闭
        self._tick_with([100])    # A 首次出现 → 冷启动
        self.assertEqual(len(self.fired), 1)

    def test_a_still_running_extra_pid_no_trigger(self):
        """A 一直运行，用户再次双击产生新 PID：不算重新打开"""
        self.eng.apply_tasks([self.task], prime=True)
        self._tick_with([])
        self._tick_with([100])          # 第一次冷启动，触发
        for _ in range(3):
            self._tick_with([100])      # 持续运行
        self._tick_with([100, 101])     # 新开窗口产生的额外 PID
        self._tick_with([100, 101, 102])
        self.assertEqual(len(self.fired), 1, "A 已运行时的新 PID 不应触发 B")

    def test_a_close_then_reopen_triggers_again(self):
        """A 完全关闭后重新打开：再次触发"""
        self.eng.apply_tasks([self.task], prime=True)
        self._tick_with([])
        self._tick_with([100])   # 第一次
        self._tick_with([])      # 完全关闭
        self._tick_with([200])   # 重新打开（新 PID）
        self.assertEqual(len(self.fired), 2)

    def test_every_new_process_mode_respects_cold_start(self):
        """“每个新进程”模式也不得对已运行的 A 触发"""
        self.task.conditions.frequency = "every_new_process"
        self.eng.apply_tasks([self.task], prime=True)
        for pids in ([100], [100], [100, 101], [100, 101, 102]):
            self._tick_with(pids)
        self.assertEqual(len(self.fired), 0)

    def test_interval_mode_no_trigger_if_a_never_closed(self):
        """定时模式：A 从软件启动起就一直开着，不得触发"""
        self.task.conditions.frequency = "interval"
        self.task.conditions.interval_sec = 1
        self.eng.apply_tasks([self.task], prime=True)
        for _ in range(5):
            self._tick_with([100])
        self.assertEqual(len(self.fired), 0)

    def test_legacy_fire_if_already_running_ignored(self):
        """旧配置里勾选过“已运行也触发”也必须被忽略"""
        self.task.conditions.fire_if_already_running = True
        self.eng.apply_tasks([self.task], prime=True)
        self._tick_with([100])
        self.assertEqual(len(self.fired), 0)

    def test_title_matching_running_window_no_trigger(self):
        """按窗口标题匹配时，已开窗口切回/改标题也不触发"""
        self.task.trigger = Trigger(field="title", value="文档", match="contains")
        self.eng.apply_tasks([self.task], prime=True)
        for titles in ({100: ["文档1.txt"]}, {100: ["文档2.txt"]}, {100: ["文档1.txt", "文档2.txt"]}):
            self._tick_with([100], titles)
        self.assertEqual(len(self.fired), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
