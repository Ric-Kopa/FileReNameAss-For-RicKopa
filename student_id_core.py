# -*- coding: utf-8 -*-
"""学号补零工具 —— 核心逻辑（不依赖图形界面，便于测试和复用）。

补零规则
--------
学号倒数第二位是班级号（默认 ``4``），最后一位是 ``1~9`` 时，在最后一位
前补 ``0``：

    202404041  -> 2024040401
    2024040410 -> 2024040410（不变）

删除字符
--------
从网上/表格里复制来的学号经常夹着 ``-``、``+``、``_``、空格等字符：

    2024-0404-1  -> 202404041  -> 2024040401
    2024040+41   -> 202404041  -> 2024040401

``clean_line`` 负责这一层清理，删除的字符集合由界面传入，默认 ``-+_``。
此外还会顺手处理全角数字（０-９ → 0-9）、全角符号、零宽字符和 BOM。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: 版本号（界面标题和“关于”里显示）
APP_VERSION = "2.1"

#: 默认要删除的字符
DEFAULT_STRIP_CHARS = "-+_"

#: 学号里常见、但复制粘贴时容易混进来的不可见字符
ZERO_WIDTH_CHARS = (
    "\u200b"  # 零宽空格
    "\u200c"  # 零宽非连接符
    "\u200d"  # 零宽连接符
    "\u2060"  # 词连接符
    "\ufeff"  # BOM
)

# 全角 ASCII（！-～）与几个“长得像空格”的字符 → 半角
_HALFWIDTH_TABLE = str.maketrans(
    {**{code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)},
     0x3000: 0x20,  # 全角空格
     0x00A0: 0x20,  # 不换行空格
     0x3007: 0x30}  # 〇 → 0
)

# 学号可能的“末位学生序号”
_STUDENT_INDEX = "123456789"


def normalize_width(text: str) -> str:
    """把全角数字/符号（含全角空格、不换行空格）转成半角。"""
    return text.translate(_HALFWIDTH_TABLE)


def remove_invisible(text: str) -> str:
    """去掉零宽字符、BOM 等看不见但会碍事的字符。"""
    return text.translate({ord(ch): None for ch in ZERO_WIDTH_CHARS})


def is_valid_class_no(value: str) -> bool:
    """班级号必须是 1 位数字。"""
    return isinstance(value, str) and len(value) == 1 and value.isdigit()


def clean_line(
    line: str,
    strip_chars: str = DEFAULT_STRIP_CHARS,
    *,
    halfwidth: bool = True,
    remove_whitespace: bool = True,
) -> tuple[str, int]:
    """清理一行文本，返回 ``(清理后的文本, 删掉的字符数)``。

    处理顺序：去首尾空白 → 去零宽字符 → 全角转半角 → 删除指定字符 →
    去掉内部空白（仅当去掉后整行都是数字时才这么做，避免破坏多列数据）。
    """
    text = remove_invisible((line or "").strip())
    if halfwidth:
        text = normalize_width(text)

    before = text
    # dict.fromkeys 既去重又保持顺序；用 replace 而不是正则，避免 - + * 等被当元字符
    for ch in dict.fromkeys(strip_chars or ""):
        if ch:
            text = text.replace(ch, "")
    text = text.strip()

    if remove_whitespace:
        compact = "".join(text.split())
        if compact.isdigit():
            text = compact

    return text, len(before) - len(text)


#: 补零后学号的标准长度（原 9 位 + 补的 0）
FULL_ID_LENGTH = 10


def fix_student_id(s: str, class_no: str = "4") -> str:
    """按规则修复单个学号；不符合规则时原样返回。

    只有长度不足 10 位（还没补过 0）时才补，否则原样返回——
    否则班级号是 1/2/3 时，已经补好的学号会被再补一次。
    """
    s = (s or "").strip()
    if len(s) >= FULL_ID_LENGTH:
        return s
    if len(s) >= 2 and s[-2] == class_no and s[-1] in _STUDENT_INDEX:
        return s[:-1] + "0" + s[-1]
    return s


@dataclass
class ProcessResult:
    """一次处理的结果与统计信息。"""

    lines: list[str] = field(default_factory=list)
    total: int = 0                                   # 非空的行数
    fixed: int = 0                                   # 实际补了 0 的条数
    chars_removed: int = 0                           # 删掉的字符总数
    duplicates: int = 0                              # 去重时跳过的条数
    emptied: int = 0                                 # 清理后变成空行的条数
    invalid: list[tuple[int, str]] = field(default_factory=list)  # (行号, 内容)

    @property
    def output(self) -> str:
        """结果文本（每行一个学号）。"""
        return "\n".join(self.lines)

    def summary(self) -> str:
        """给状态栏用的一句话统计。"""
        parts = [f"共 {self.total} 条", f"补零 {self.fixed} 条",
                 f"删除 {self.chars_removed} 个字符"]
        if self.duplicates:
            parts.append(f"重复跳过 {self.duplicates} 条")
        if self.emptied:
            parts.append(f"清理后为空 {self.emptied} 条")
        if self.invalid:
            parts.append(f"非数字 {len(self.invalid)} 行")
        return "，".join(parts)


def process_text(
    text: str,
    class_no: str = "4",
    strip_chars: str = DEFAULT_STRIP_CHARS,
    *,
    halfwidth: bool = True,
    remove_whitespace: bool = True,
    dedupe: bool = False,
    pad: bool = True,
) -> ProcessResult:
    """处理整段文本（每行一个学号）。

    ``pad=False`` 时只清理字符、不补零（“仅清理字符”按钮用）。
    非数字的行不会丢：保留清理后的样子写进结果，同时记到 ``invalid`` 里。
    """
    result = ProcessResult()
    seen: set[str] = set()

    for lineno, raw in enumerate((text or "").splitlines(), 1):
        if not raw.strip():
            continue
        result.total += 1

        cleaned, removed = clean_line(
            raw,
            strip_chars,
            halfwidth=halfwidth,
            remove_whitespace=remove_whitespace,
        )
        result.chars_removed += removed

        if not cleaned:
            result.emptied += 1
            continue

        if not cleaned.isdigit():
            result.invalid.append((lineno, cleaned))
            result.lines.append(cleaned)
            continue

        if dedupe:
            if cleaned in seen:
                result.duplicates += 1
                continue
            seen.add(cleaned)

        if pad:
            padded = fix_student_id(cleaned, class_no)
            if padded != cleaned:
                result.fixed += 1
            result.lines.append(padded)
        else:
            result.lines.append(cleaned)

    return result


def process_file(path: str, *args, **kwargs) -> ProcessResult:
    """读取文本文件并处理；编码依次尝试 utf-8-sig / utf-8 / gb18030。"""
    return process_text(read_text_file(path), *args, **kwargs)


def read_text_file(path: str) -> str:
    """按常见中文编码尝试读取文本，避免 ``errors='ignore'`` 丢字。"""
    with open(path, "rb") as f:
        data = f.read()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


#: 拖入文件夹时，认作“学号文本”的后缀
TEXT_SUFFIXES = (".txt", ".csv", ".tsv", ".log", ".md", ".dat")

#: 一次最多导入的文件数（防止误拖整个盘符）
MAX_IMPORT_FILES = 300


@dataclass
class ImportBundle:
    """导入一批文件/文件夹后得到的内容与统计。"""

    text: str = ""
    files: list[str] = field(default_factory=list)      # 实际读取的文件
    errors: list[tuple[str, str]] = field(default_factory=list)  # (路径, 原因)

    @property
    def lines(self) -> int:
        return len([ln for ln in self.text.splitlines() if ln.strip()])

    def summary(self) -> str:
        parts = [f"{len(self.files)} 个文件", f"{self.lines} 行"]
        if self.errors:
            parts.append(f"{len(self.errors)} 个读取失败")
        return "，".join(parts)


def _iter_text_files(folder: str, *, recursive: bool) -> list[str]:
    """列出文件夹里的文本文件（按路径排序，后缀不区分大小写）。"""
    found: list[str] = []
    if recursive:
        for root, dirs, names in os.walk(folder):
            dirs.sort()
            for name in sorted(names):
                if name.lower().endswith(TEXT_SUFFIXES):
                    found.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.lower().endswith(TEXT_SUFFIXES):
                found.append(path)
    return found


def read_paths(paths, *, recursive: bool = True) -> ImportBundle:
    """读取拖进来/选中的文件与文件夹，合并成一段文本。

    文件夹只取 ``TEXT_SUFFIXES`` 里的文本文件；直接选中的文件不论后缀都会读。
    读不了的文件跳过并记在 ``errors`` 里，不中断其它文件。
    """
    bundle = ImportBundle()
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]

    targets: list[str] = []
    for raw in paths:
        path = os.path.abspath(str(raw))
        if os.path.isdir(path):
            targets.extend(_iter_text_files(path, recursive=recursive))
        elif os.path.isfile(path):
            targets.append(path)

    # 去重但保持顺序
    targets = list(dict.fromkeys(targets))
    if len(targets) > MAX_IMPORT_FILES:
        targets = targets[:MAX_IMPORT_FILES]

    chunks: list[str] = []
    for path in targets:
        try:
            data = read_text_file(path)
        except OSError as exc:
            bundle.errors.append((path, str(exc)))
            continue
        if not data.strip():
            continue
        bundle.files.append(path)
        chunks.append(data.strip("\n"))

    bundle.text = "\n".join(chunks)
    return bundle
