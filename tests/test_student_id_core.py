# -*- coding: utf-8 -*-
"""student_id_core 的单元测试（不依赖图形界面）。

运行：python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from student_id_core import (  # noqa: E402
    DEFAULT_STRIP_CHARS,
    clean_line,
    fix_student_id,
    is_valid_class_no,
    looks_binary,
    process_file,
    process_text,
    read_paths,
    read_text_file,
)


class FixStudentIdTest(unittest.TestCase):
    def test_pad_single_digit_index(self):
        self.assertEqual(fix_student_id("202404041"), "2024040401")
        self.assertEqual(fix_student_id("202404049"), "2024040409")
        self.assertEqual(fix_student_id("202404042"), "2024040402")

    def test_already_padded_unchanged(self):
        self.assertEqual(fix_student_id("2024040410"), "2024040410")

    def test_ten_digit_ids_are_never_padded_twice(self):
        # 班级号 1/2/3 时，已经补好的 10 位学号倒数第二位正好等于班级号，
        # 旧规则会再补一次；现在按长度拦住。
        for class_no in ("1", "2", "3", "4"):
            for tail in ("1", "2", "3", "9"):
                already = f"20240404{class_no}{tail}"   # 10 位
                self.assertEqual(fix_student_id(already, class_no), already)
        self.assertEqual(fix_student_id("2024040411", "1"), "2024040411")
        self.assertEqual(fix_student_id("2024040423", "2"), "2024040423")
        self.assertEqual(fix_student_id("2024040433", "3"), "2024040433")

    def test_longer_ids_unchanged(self):
        self.assertEqual(fix_student_id("20240404011", "1"), "20240404011")
        self.assertEqual(fix_student_id("202404040123"), "202404040123")

    def test_class_123_still_pads_nine_digit_ids(self):
        self.assertEqual(fix_student_id("202404011", "1"), "2024040101")
        self.assertEqual(fix_student_id("202404022", "2"), "2024040202")
        self.assertEqual(fix_student_id("202404033", "3"), "2024040303")

    def test_index_zero_unchanged(self):
        self.assertEqual(fix_student_id("202404040"), "202404040")

    def test_last_two_digits_must_match_class(self):
        self.assertEqual(fix_student_id("202404051"), "202404051")

    def test_other_class_no(self):
        self.assertEqual(fix_student_id("202403031", "3"), "2024030301")
        self.assertEqual(fix_student_id("202403031"), "202403031")

    def test_short_and_empty(self):
        self.assertEqual(fix_student_id(""), "")
        self.assertEqual(fix_student_id("4"), "4")
        self.assertEqual(fix_student_id("41"), "401")

    def test_strip_spaces(self):
        self.assertEqual(fix_student_id("  202404041  "), "2024040401")


class CleanLineTest(unittest.TestCase):
    def test_remove_default_chars(self):
        self.assertEqual(clean_line("2024-0404-1"), ("202404041", 2))
        self.assertEqual(clean_line("+2024040410"), ("2024040410", 1))
        self.assertEqual(clean_line("2024_0404_1"), ("202404041", 2))

    def test_remove_mixed(self):
        self.assertEqual(clean_line("20-24_04+04-1"), ("202404041", 4))

    def test_fullwidth_digits_and_symbols(self):
        self.assertEqual(clean_line("２０２４０４０４１"), ("202404041", 0))
        self.assertEqual(clean_line("2024－0404－1"), ("202404041", 2))

    def test_internal_whitespace_removed_only_for_digits(self):
        self.assertEqual(clean_line("2024 0404 1"), ("202404041", 2))
        self.assertEqual(clean_line("2024\t0404\t1"), ("202404041", 2))
        # 多列数据（学号 + 姓名）不能被空格粘在一起
        self.assertEqual(clean_line("2024 0404 1 张三"), ("2024 0404 1 张三", 0))

    def test_whitespace_can_be_kept(self):
        self.assertEqual(clean_line("2024 0404 1", remove_whitespace=False),
                         ("2024 0404 1", 0))

    def test_invisible_chars(self):
        self.assertEqual(clean_line("20240404\u200b1"), ("202404041", 0))
        self.assertEqual(clean_line("\ufeff202404041"), ("202404041", 0))

    def test_custom_strip_chars(self):
        self.assertEqual(clean_line("2024/0404/1", "/"), ("202404041", 2))
        self.assertEqual(clean_line("2024/0404/1"), ("2024/0404/1", 0))

    def test_halfwidth_can_be_disabled(self):
        self.assertEqual(clean_line("2024－0404－1", halfwidth=False),
                         ("2024－0404－1", 0))

    def test_only_strip_chars_left(self):
        self.assertEqual(clean_line(" -_- "), ("", 3))

    def test_empty(self):
        self.assertEqual(clean_line(""), ("", 0))
        self.assertEqual(clean_line(None), ("", 0))


class ProcessTextTest(unittest.TestCase):
    def test_mixed_input(self):
        text = (
            "202404041\n"
            "2024-0404-2\n"
            "\n"
            "2024040+43\n"
            "2024040410\n"
            "学号202404047\n"
            "----\n"
        )
        r = process_text(text)
        self.assertEqual(r.lines, ["2024040401", "2024040402", "2024040403",
                                   "2024040410", "学号202404047"])
        self.assertEqual(r.total, 6)
        self.assertEqual(r.fixed, 3)
        self.assertEqual(r.chars_removed, 7)  # 3 个分隔符 + “----” 里的 4 个
        self.assertEqual(r.emptied, 1)
        self.assertEqual(r.invalid, [(6, "学号202404047")])

    def test_output_joins_lines(self):
        r = process_text("202404041\n202404042")
        self.assertEqual(r.output, "2024040401\n2024040402")

    def test_clean_only_mode(self):
        r = process_text("2024-0404-1\n2024+0404+2", pad=False)
        self.assertEqual(r.lines, ["202404041", "202404042"])
        self.assertEqual(r.fixed, 0)
        self.assertEqual(r.chars_removed, 4)

    def test_dedupe(self):
        text = "2024-0404-1\n202404041\n202404042"
        r = process_text(text, dedupe=True)
        self.assertEqual(r.lines, ["2024040401", "2024040402"])
        self.assertEqual(r.duplicates, 1)

    def test_no_dedupe_by_default(self):
        r = process_text("202404041\n202404041")
        self.assertEqual(r.lines, ["2024040401", "2024040401"])

    def test_custom_class_no(self):
        r = process_text("202403031", class_no="3")
        self.assertEqual(r.lines, ["2024030301"])

    def test_idempotent(self):
        once = process_text("2024-0404-1").output
        twice = process_text(once).output
        self.assertEqual(once, twice)

    def test_blank_input(self):
        r = process_text("   \n\n")
        self.assertEqual(r.lines, [])
        self.assertEqual(r.total, 0)
        self.assertEqual(r.output, "")

    def test_summary_mentions_counts(self):
        r = process_text("202404041\n2024-0404-2\n学号3")
        self.assertIn("共 3 条", r.summary())
        self.assertIn("补零 2 条", r.summary())
        self.assertIn("非数字 1 行", r.summary())


class FileHelpersTest(unittest.TestCase):
    def setUp(self):
        # 直接放在 tests/ 下：部分沙箱环境不允许往运行时新建的子目录里写文件
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.paths: list[str] = []

    def tearDown(self):
        for path in self.paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def _path(self, name: str) -> str:
        path = os.path.join(self.dir, name)
        self.paths.append(path)
        return path

    def test_read_gbk_file(self):
        path = self._path("_test_ids_gbk.txt")
        with open(path, "wb") as f:
            f.write("2024-0404-1\n学号\n".encode("gbk"))
        self.assertIn("学号", read_text_file(path))
        r = process_file(path)
        self.assertEqual(r.lines[0], "2024040401")

    def test_read_utf8_bom_file(self):
        path = self._path("_test_ids_utf8.txt")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("202404041\n")
        self.assertEqual(read_text_file(path).strip(), "202404041")


class ReadPathsTest(unittest.TestCase):
    """读取文件 / 整个文件夹（拖放导入用）。"""

    def setUp(self):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.folder = os.path.join(self.dir, "_test_import_dir")
        os.makedirs(os.path.join(self.folder, "sub"), exist_ok=True)
        self.paths: list[str] = []
        self._write(os.path.join(self.folder, "a.txt"), "202404041\n2024-0404-2\n")
        self._write(os.path.join(self.folder, "b.csv"), "202404043\n")
        self._write(os.path.join(self.folder, "sub", "c.log"), "202404044\n")
        self._write(os.path.join(self.folder, "not-text.bin"), "Binary\x00data")

    def tearDown(self):
        for path in self.paths:
            try:
                os.remove(path)
            except OSError:
                pass
        for name in ("sub", ""):
            try:
                os.rmdir(os.path.join(self.folder, name))
            except OSError:
                pass

    def _write(self, path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.paths.append(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_folder_recursive_collect(self):
        bundle = read_paths([self.folder])
        self.assertEqual(len(bundle.files), 3)          # 不含 not-text.bin
        self.assertEqual(bundle.lines, 4)
        self.assertIn("202404041", bundle.text)
        self.assertIn("202404044", bundle.text)
        self.assertNotIn("Binary", bundle.text)

    def test_folder_non_recursive(self):
        bundle = read_paths([self.folder], recursive=False)
        self.assertEqual(len(bundle.files), 2)

    def test_single_file_any_suffix(self):
        weird = os.path.join(self.folder, "ids.bin")
        self._write(weird, "202404045\n")
        bundle = read_paths([weird])
        self.assertEqual(len(bundle.files), 1)
        self.assertIn("202404045", bundle.text)

    def test_missing_path_is_ignored(self):
        bundle = read_paths([os.path.join(self.folder, "no-such-file.txt")])
        self.assertEqual(bundle.files, [])
        self.assertEqual(bundle.text, "")

    def test_binary_file_is_skipped(self):
        """直接拖入 xlsx / docx 这类二进制文件时要跳过，而不是读成乱码卡死界面。"""
        binary = os.path.join(self.folder, "students.xlsx")
        with open(binary, "wb") as f:
            f.write(b"PK\x03\x04\x00\x00\x00\x00Excel data\x00\x00")
        bundle = read_paths([binary])
        self.assertEqual(bundle.files, [])
        self.assertEqual(bundle.text, "")
        self.assertEqual(len(bundle.errors), 1)
        self.assertIn("二进制", bundle.errors[0][1])

    def test_oversized_file_is_skipped(self):
        import unittest.mock as mock

        big = os.path.join(self.folder, "big.txt")
        self._write(big, "202404041\n" * 200)
        with mock.patch("student_id_core.MAX_FILE_BYTES", 16):
            bundle = read_paths([big])
        self.assertEqual(bundle.files, [])
        self.assertIn("太大", bundle.errors[0][1])

    def test_looks_binary_heuristic(self):
        self.assertTrue(looks_binary(b"PK\x03\x04\x00\x00"))
        self.assertTrue(looks_binary(bytes(range(0, 32)) * 8))
        self.assertFalse(looks_binary("202404041\n学号\n".encode("utf-8")))
        self.assertFalse(looks_binary(b""))

    def test_text_flows_into_process_text(self):
        bundle = read_paths([self.folder])
        result = process_text(bundle.text)
        self.assertEqual(result.total, 4)
        self.assertEqual(result.fixed, 4)


class HelpersTest(unittest.TestCase):
    def test_valid_class_no(self):
        self.assertTrue(is_valid_class_no("4"))
        self.assertTrue(is_valid_class_no("0"))
        self.assertFalse(is_valid_class_no(""))
        self.assertFalse(is_valid_class_no("44"))
        self.assertFalse(is_valid_class_no("a"))
        self.assertFalse(is_valid_class_no(None))

    def test_default_strip_chars(self):
        self.assertEqual(DEFAULT_STRIP_CHARS, "-+_")


if __name__ == "__main__":
    unittest.main()
