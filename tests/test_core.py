# -*- coding: utf-8 -*-
"""核心逻辑单元测试： python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
