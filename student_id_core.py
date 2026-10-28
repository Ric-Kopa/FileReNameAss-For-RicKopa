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
import re
import shutil
import time
from dataclasses import dataclass, field

#: 版本号（界面标题和“关于”里显示）
APP_VERSION = "2.5.1"

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


def rewrite_leading_id(text: str, class_no: str = "4") -> str | None:
    """把**行首**的学号换成补零后的，后面的姓名/作业名原样保留。

    ``202404041 张三 作业一`` → ``2024040401 张三 作业一``
    行首不是学号（或后面没有文字）时返回 ``None``。
    """
    matched = _match_name_id(text)
    if not matched:
        return None
    digits, rest = matched
    if not _split_rest(rest):
        # 整行只有一个学号（可能夹着空格/分隔符），交给常规的清理+补零流程
        return None
    fixed = fix_student_id(digits, class_no)
    return f"{fixed}{rest}"


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

        if cleaned.isdigit():
            final = fix_student_id(cleaned, class_no) if pad else cleaned
            before = cleaned
        else:
            # 形如「202404041 张三 作业一」：只把学号补零，后面的文字（含分隔符）原样保留
            original = remove_invisible((raw or "").strip())
            if halfwidth:
                original = normalize_width(original)
            final = rewrite_leading_id(original, class_no) if pad else None
            if final is None:
                result.invalid.append((lineno, cleaned))
                result.lines.append(cleaned)
                continue
            before = original

        if dedupe:
            if final in seen:
                result.duplicates += 1
                continue
            seen.add(final)

        if final != before:
            result.fixed += 1
        result.lines.append(final)

    return result


def process_file(path: str, *args, **kwargs) -> ProcessResult:
    """读取文本文件并处理；编码依次尝试 utf-8-sig / utf-8 / gb18030。"""
    return process_text(read_text_file(path), *args, **kwargs)


def _decode_bytes(data: bytes) -> str:
    """按常见中文编码尝试解码，避免 ``errors='ignore'`` 丢字。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_text_file(path: str) -> str:
    """按常见中文编码尝试读取文本。"""
    with open(path, "rb") as f:
        data = f.read()
    return _decode_bytes(data)


def looks_binary(data: bytes) -> bool:
    """粗略判断是不是二进制文件（Excel / Word / PDF / 图片等）。

    拖进来的如果是 xlsx、docx、pdf 这类文件，硬当文本读会解码出几兆乱码，
    界面会直接卡死，所以先嗅一下：含 NUL 字节、或控制字符过多就当作二进制。
    """
    sample = data[:8192]
    if not sample:
        return False
    if b"\0" in sample:
        return True
    control = sum(1 for byte in sample if byte < 9 or 13 < byte < 32)
    return control / len(sample) > 0.05


#: 拖入文件夹时，认作“学号文本”的后缀
TEXT_SUFFIXES = (".txt", ".csv", ".tsv", ".log", ".md", ".dat")

#: 一次最多导入的文件数（防止误拖整个盘符）
MAX_IMPORT_FILES = 300

#: 单个文件大小上限（超过就不读，避免把界面卡死）
MAX_FILE_BYTES = 20 * 1024 * 1024

#: 合并后的文本长度上限（字符数）
MAX_TOTAL_CHARS = 2_000_000

#: 文件名开头的学号：数字开头 + 数字/分隔符，至少 6 位数字
_NAME_ID_RE = re.compile(r"^\s*([0-9][0-9\-\+_\. ]{5,}[0-9])")


def _match_name_id(text: str) -> tuple[str, str] | None:
    """匹配开头的学号，返回 ``(纯数字学号, 后面的剩余文字)``。

    学号最多按 ``FULL_ID_LENGTH`` 位截断：像 ``2517970408-08-李四`` 这种
    “学号后面又跟了一段编号”的写法，只取前 10 位数字，后面的留作文字。
    """
    match = _NAME_ID_RE.match(text)
    if not match:
        return None
    core = match.group(1)
    if core[-1] == " ":
        return None
    rest = text[match.end():]
    if rest[:1].isdigit():
        return None
    digits = re.sub(r"[^0-9]", "", core)
    if len(digits) > FULL_ID_LENGTH:
        seen = 0
        cut = len(core)
        for index, ch in enumerate(core):
            if ch.isdigit():
                seen += 1
                if seen == FULL_ID_LENGTH:
                    cut = index + 1
                    break
        rest = core[cut:] + rest
        digits = digits[:FULL_ID_LENGTH]
        if rest[:1].isdigit():
            return None
    if len(digits) < 6:
        return None
    return digits, rest


def student_id_from_name(filename: str, *, min_digits: int = 6) -> str | None:
    """从文件名里取学号，例如 ``25179704-08-何金森-作业一.doc`` → ``2517970408``。

    只认**文件名开头**的数字（允许中间夹 ``- + _ . 空格``），后面必须紧跟非数字字符，
    所以「作业一.doc」「第3组.doc」这类不会被误认成学号。
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    matched = _match_name_id(stem)
    if not matched or len(matched[0]) < min_digits:
        return None
    return matched[0]


#: 从文件名生成输出行时的格式
NAME_FORMAT_SPLIT = "split"   # 学号 + 空格分隔的剩余文字（默认）
NAME_FORMAT_KEEP = "keep"     # 保留原文件名，只把开头的学号换成补零后的
NAME_FORMAT_NOEXT = "noext"   # 同 keep，但去掉扩展名

#: 界面下拉框里的顺序与说明
NAME_FORMATS = (
    (NAME_FORMAT_SPLIT, "学号 + 空格分开的文字（推荐）"),
    (NAME_FORMAT_KEEP, "保留原文件名（含扩展名），只换学号"),
    (NAME_FORMAT_NOEXT, "保留原文件名，去掉扩展名"),
)

_SPLIT_SEPS = re.compile(r"[\s\-+_.·、,，;；/\\|~～—–]+")


def _split_rest(rest: str) -> str:
    """把学号后面的 ``-08-何金森-作业一`` 拆成空格分隔的文字。"""
    return " ".join(part for part in _SPLIT_SEPS.split(rest) if part)


def rewrite_name(filename: str, class_no: str = "4", *,
                 fmt: str = NAME_FORMAT_SPLIT) -> str | None:
    """把文件名开头的学号换成补零后的学号，**其余文字原样保留**。

    例：``25179704-08-何金森-作业一.doc``

    - ``split``（默认）→ ``2517970408 何金森 作业一``
    - ``keep``        → ``2517970408-何金森-作业一.doc``
    - ``noext``       → ``2517970408-何金森-作业一``
    """
    base = os.path.basename(filename)
    stem, ext = os.path.splitext(base)
    matched = _match_name_id(stem)
    if not matched:
        return None
    digits, rest = matched
    fixed = fix_student_id(digits, class_no)
    if fmt == NAME_FORMAT_KEEP:
        return f"{fixed}{rest}{ext}"
    if fmt == NAME_FORMAT_NOEXT:
        return f"{fixed}{rest}"
    tail = _split_rest(rest)
    return f"{fixed} {tail}" if tail else fixed


@dataclass
class ImportBundle:
    """导入一批文件/文件夹后得到的内容与统计。"""

    text: str = ""
    files: list[str] = field(default_factory=list)      # 实际读取内容的文件
    names: list[str] = field(default_factory=list)      # 从文件名取到学号的文件
    errors: list[tuple[str, str]] = field(default_factory=list)  # (路径, 原因)
    source_dir: str = ""                                # 拖入的文件夹（用于“保存到源文件夹”）

    @property
    def lines(self) -> int:
        return len([ln for ln in self.text.splitlines() if ln.strip()])

    def summary(self) -> str:
        parts = []
        if self.files:
            parts.append(f"{len(self.files)} 个文本文件")
        if self.names:
            parts.append(f"{len(self.names)} 个文件名")
        if not parts:
            parts.append("0 个文件")
        parts.append(f"{self.lines} 行")
        if self.errors:
            parts.append(f"{len(self.errors)} 个跳过")
        return "，".join(parts)


def _iter_folder_files(folder: str, *, recursive: bool) -> list[str]:
    """列出文件夹里的全部文件（按路径排序）。"""
    found: list[str] = []
    if recursive:
        for root, dirs, names in os.walk(folder):
            dirs.sort()
            for name in sorted(names):
                found.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                found.append(path)
    return found


def read_paths(paths, *, recursive: bool = True, from_names: bool = True,
               name_format: str = NAME_FORMAT_SPLIT,
               class_no: str = "4") -> ImportBundle:
    """读取拖进来/选中的文件与文件夹，合并成一段文本。

    - 文本文件（内容确实是文本）读取内容；
    - Word / Excel / PDF 等读不了的文件，``from_names=True`` 时从**文件名**解析，
      保留学号后面的姓名/作业名（格式见 ``NAME_FORMATS``）：
      ``25179704-08-何金森-作业一.doc`` → ``2517970408 何金森 作业一``；
    - 读不了又取不到学号的文件记在 ``errors`` 里，不中断其它文件。
    """
    bundle = ImportBundle()
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]

    targets: list[str] = []
    first_folder = ""
    for raw in paths:
        path = os.path.abspath(str(raw))
        if os.path.isdir(path):
            first_folder = first_folder or path
            targets.extend(_iter_folder_files(path, recursive=recursive))
        elif os.path.isfile(path):
            first_folder = first_folder or os.path.dirname(path)
            targets.append(path)
    bundle.source_dir = first_folder

    # 去重但保持顺序
    targets = list(dict.fromkeys(targets))
    if len(targets) > MAX_IMPORT_FILES:
        targets = targets[:MAX_IMPORT_FILES]

    chunks: list[str] = []
    total_chars = 0
    for path in targets:
        name_line = (rewrite_name(path, class_no, fmt=name_format)
                     if from_names else None)

        def _use_name(reason: str) -> None:
            """用文件名里的学号顶上；取不到才记为跳过。"""
            if name_line:
                bundle.names.append(path)
                chunks.append(name_line)
            else:
                bundle.errors.append((path, reason))

        try:
            size = os.path.getsize(path)
        except OSError as exc:
            bundle.errors.append((path, str(exc)))
            continue
        if size > MAX_FILE_BYTES:
            _use_name(f"文件太大（{size / 1048576:.1f} MB），已跳过（上限 "
                      f"{MAX_FILE_BYTES / 1048576:.0f} MB）")
            continue
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            bundle.errors.append((path, str(exc)))
            continue
        if looks_binary(raw):
            _use_name("是二进制文件（Word / Excel / PDF 等），改为从文件名取学号")
            continue
        data = _decode_bytes(raw)
        if not data.strip():
            _use_name("文件是空的")
            continue
        bundle.files.append(path)
        total_chars += len(data)
        chunks.append(data.strip("\n"))
        if total_chars > MAX_TOTAL_CHARS:
            bundle.errors.append((path, "内容太多，后面的文件已停止读取"))
            break

    bundle.text = "\n".join(chunks)
    return bundle

def plan_renames(paths, class_no: str = "4", *,
                 fmt: str = NAME_FORMAT_SPLIT) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """算出每个文件改学号后的新文件名。

    返回 ``(可改名的 [(源路径, 新文件名)], 跳过的 [(路径, 原因)])``。
    扩展名一律保留；``fmt=split`` 时学号后面的分隔符换成空格。
    """
    plans: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []
    for raw in paths:
        path = os.path.abspath(str(raw))
        stem, ext = os.path.splitext(os.path.basename(path))
        matched = _match_name_id(stem)
        if not matched:
            skipped.append((path, "文件名开头没有学号"))
            continue
        digits, rest = matched
        fixed = fix_student_id(digits, class_no)
        if fmt == NAME_FORMAT_SPLIT:
            tail = _split_rest(rest)
            new_stem = f"{fixed} {tail}".strip()
        else:
            new_stem = f"{fixed}{rest}"
        plans.append((path, f"{new_stem}{ext}"))
    return plans, skipped


def export_files(plans, target_dir: str, *, copy: bool = True,
                 errors: list | None = None) -> list[tuple[str, str]]:
    """按 ``plan_renames`` 的结果复制（或直接重命名）文件，返回 ``[(源, 目标)]``。

    目标已有同名文件时自动加 `` (2)`` `` (3)`` 后缀，不会覆盖别人的文件。
    传了 ``errors`` 时不会因为单个文件失败而中断，失败的会追加为
    ``(源, 目标, 异常)``。
    """
    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    used: set[str] = set()
    done: list[tuple[str, str]] = []
    for src, new_name in plans:
        src = os.path.abspath(src)
        stem, ext = os.path.splitext(new_name)
        candidate = new_name
        index = 2
        while True:
            dest = os.path.join(target_dir, candidate)
            same = os.path.abspath(dest) == src
            taken = candidate.lower() in used or (os.path.exists(dest) and not same)
            if not taken:
                break
            candidate = f"{stem} ({index}){ext}"
            index += 1
        dest = os.path.join(target_dir, candidate)
        if os.path.abspath(dest) == src:
            done.append((src, dest))       # 名字本来就对，什么都不用做
            used.add(candidate.lower())
            continue
        def _do() -> None:
            if copy:
                try:
                    shutil.copy2(src, dest)
                except OSError:
                    # 目标文件夹不支持复制元数据时退一步，只拷内容
                    if os.path.exists(dest):
                        raise
                    shutil.copyfile(src, dest)
            else:
                os.replace(src, dest)

        try:
            try:
                _do()
            except OSError:
                # 杀毒软件 / OneDrive 同步偶尔会短暂占用，等一下再试一次
                time.sleep(0.2)
                _do()
        except OSError as exc:
            if errors is not None:
                errors.append((src, dest, exc))
                continue
            raise
        used.add(candidate.lower())
        done.append((src, dest))
    return done

