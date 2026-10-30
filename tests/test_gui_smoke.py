# -*- coding: utf-8 -*-
"""图形界面的冒烟测试：能建窗口、能按按钮逻辑得到正确结果。

运行：python -m unittest discover -s tests -v
没有图形环境（例如纯命令行服务器）时自动跳过。
"""

import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

import main as app_module  # noqa: E402


class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = app_module.App()
        except tk.TclError as e:  # 没有图形界面
            raise unittest.SkipTest(f"无图形环境：{e}")
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        app = self.app
        app.class_var.set("4")
        app.strip_var.set("-+_")
        app.whitespace_var.set(True)
        app.halfwidth_var.set(True)
        app.dedupe_var.set(False)
        app.do_clear()

    def _fix(self, text: str) -> str:
        self.app.do_clear()
        self.app.in_text.insert("1.0", text)
        self.app.do_fix()
        return self.app.out_text.get("1.0", "end-1c")

    def test_window_built(self):
        self.assertIn("学号补零工具", self.app.title())
        self.assertIn("41~49", self.app.rule_label.cget("text"))

    def test_rule_hint_follows_class_no(self):
        self.app.class_var.set("3")
        self.app.update_idletasks()
        self.assertIn("31~39", self.app.rule_label.cget("text"))

    def test_fix_removes_chars_and_pads(self):
        out = self._fix("2024-0404-1\n2024040+42\n20240404_3")
        self.assertEqual(out, "2024040401\n2024040402\n2024040403")

    def test_already_padded_kept(self):
        self.assertEqual(self._fix("2024040410"), "2024040410")

    def test_clean_only_does_not_pad(self):
        self.app.in_text.insert("1.0", "2024-0404-1")
        self.app.do_clean()
        self.assertEqual(self.app.out_text.get("1.0", "end-1c"), "202404041")

    def test_dedupe_option(self):
        self.app.dedupe_var.set(True)
        out = self._fix("2024-0404-1\n202404041")
        self.assertEqual(out, "2024040401")
        self.assertEqual(self.app.last_result.duplicates, 1)

    def test_invalid_lines_reported(self):
        out = self._fix("202404041\n学号202404047")
        self.assertIn("学号202404047", out)
        self.assertEqual(len(self.app.last_result.invalid), 1)
        self.assertIn("非数字", self.app.status.cget("text"))

    def test_whitespace_option_off_keeps_spaces(self):
        self.app.whitespace_var.set(False)
        self.assertEqual(self._fix("2024 0404 1"), "2024 0404 1")

    def test_fullwidth_option(self):
        self.assertEqual(self._fix("２０２４０４０４１"), "2024040401")
        self.app.halfwidth_var.set(False)
        self.assertEqual(self._fix("２０２４０４０４１"), "２０２４０４０４１")

    def test_strip_presets_and_extra_combine(self):
        app = self.app
        app._apply_strip_chars("")
        self.assertEqual(app._strip_chars(), "")
        app.strip_presets["_"].set(True)
        app.strip_presets["."].set(True)
        app.strip_var.set("@#")
        self.assertEqual(app._strip_chars(), "_.@#")
        self.assertEqual(self._fix("2024.0404_1"), "2024040401")

    def test_settings_roundtrip(self):
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(tests_dir, "_test_settings.json")
        old = app_module.SETTINGS_FILE
        app_module.SETTINGS_FILE = path
        try:
            self.app.class_var.set("7")
            self.app._apply_strip_chars("/.@")
            self.app.dedupe_var.set(True)
            self.app._save_settings()
            saved = sorted(self.app._strip_chars())

            self.app.class_var.set("4")
            self.app._apply_strip_chars("-+_")
            self.app.dedupe_var.set(False)
            self.app._load_settings()

            self.assertEqual(self.app.class_var.get(), "7")
            self.assertEqual(sorted(self.app._strip_chars()), saved)   # “/”“.”回填勾选框，“@”回填其他
            self.assertTrue(self.app.dedupe_var.get())
        finally:
            app_module.SETTINGS_FILE = old
            try:
                os.remove(path)
            except OSError:
                pass

    # ---------- 拖入文件 / 文件夹 ----------
    def _make_folder(self) -> str:
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_test_drop_folder")
        os.makedirs(folder, exist_ok=True)
        for name, text in (("a.txt", "2024-0404-1\n"), ("b.csv", "2024040+42\n")):
            with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                f.write(text)
        return folder

    def _remove_folder(self, folder: str) -> None:
        for name in os.listdir(folder):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
        try:
            os.rmdir(folder)
        except OSError:
            pass

    def test_drop_folder_loads_all_text_files(self):
        folder = self._make_folder()
        try:
            self.app._load_paths([folder])
            content = self.app.in_text.get("1.0", "end-1c")
            self.assertIn("2024-0404-1", content)
            self.assertIn("2024040+42", content)
            self.assertIn("2 个文本文件", self.app.status.cget("text"))
            self.assertEqual(self.app.do_fix() or self.app.out_text.get("1.0", "end-1c"),
                             "2024040401\n2024040402")
        finally:
            self._remove_folder(folder)

    def test_drop_asks_before_replacing(self):
        folder = self._make_folder()
        old_ask = app_module.messagebox.askyesno
        try:
            self.app.in_text.insert("1.0", "202404049")
            app_module.messagebox.askyesno = lambda *a, **k: False
            self.app._load_paths([folder])
            self.assertEqual(self.app.in_text.get("1.0", "end-1c").strip(), "202404049")

            app_module.messagebox.askyesno = lambda *a, **k: True
            self.app._load_paths([folder])
            self.assertIn("2024040+42", self.app.in_text.get("1.0", "end-1c"))
        finally:
            app_module.messagebox.askyesno = old_ask
            self._remove_folder(folder)

    def test_name_format_default_and_options(self):
        self.assertEqual(self.app.name_format_var.get(), app_module.NAME_FORMATS[0][1])
        self.assertEqual(len(app_module.NAME_FORMATS), 3)
        self.assertEqual(app_module.NAME_FORMAT_BY_LABEL[app_module.NAME_FORMATS[0][1]], "split")

    def test_save_dir_uses_source_folder(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_save_src")
        os.makedirs(src, exist_ok=True)
        try:
            self.app.source_dir = src
            self.assertEqual(self.app._save_dir("source"), src)
            self.assertEqual(self.app._save_dir("subfolder"),
                             os.path.join(src, "_save_src-已处理"))
        finally:
            os.rmdir(src)

    def test_save_mode_default_is_source(self):
        self.assertEqual(self.app.save_mode_var.get(), "source")

    def test_drop_target_can_be_enabled(self):
        if sys.platform != "win32":
            self.skipTest("拖放只支持 Windows")
        self.app._enable_file_drop()
        self.assertTrue(self.app.drop_target.enabled)
        # 重复调用不应重复挂钩子
        self.app._enable_file_drop()
        self.assertTrue(self.app.drop_target.enabled)


if __name__ == "__main__":
    unittest.main()


class ExportFlowTest(unittest.TestCase):
    """“保存/导出”对话框与导出重命名后的文件。"""

    @classmethod
    def setUpClass(cls):
        cls.app = app_module.App()
        cls.app.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(__file__), "_export_case")
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.makedirs(self.tmp)
        self.src = os.path.join(self.tmp, "25179704-08-何金森-作业一.doc")
        with open(self.src, "wb") as f:
            f.write(b"x")
        self.app.file_paths = [self.src]
        self.patches = []

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self, target, value):
        p = mock.patch(target, value)
        self.patches.append(p)
        p.start()
        return p

    def test_export_dialog_can_be_opened_and_closed(self):
        app = self.app

        def close():
            for child in app.winfo_children():
                if isinstance(child, tk.Toplevel):
                    child.destroy()

        app.after(300, close)
        self.assertIsNone(app._ask_export("2517970408", [], []))

    def test_probe_write_detects_unwritable_folder(self):
        ok_dir = os.path.join(self.tmp, "ok")
        self.assertIsNone(app_module.App._probe_write(ok_dir))
        blocked = os.path.join("C:\\Windows\\System32", "dsh_no_such_dir")
        self.assertIsNotNone(app_module.App._probe_write(blocked))

    def test_export_copy_flow_writes_renamed_file(self):
        out = os.path.join(self.tmp, "out")
        self._patch("main.messagebox.askyesno", lambda *a, **k: False)
        self._patch("main.messagebox.showinfo", lambda *a, **k: None)
        self._patch("main.messagebox.showwarning", lambda *a, **k: None)
        plan = [(self.src, "2517970408 何金森 作业一.doc")]
        self._patch("main.App._ask_export", lambda *a, **k: {"action": "copy", "folder": out})
        self.app.do_save()
        self.assertEqual(sorted(os.listdir(out)), ["2517970408 何金森 作业一.doc"])
        self.assertTrue(os.path.exists(self.src))
