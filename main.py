# -*- coding: utf-8 -*-
"""学号补零小工具（图形界面）

规则：学号倒数第二位是班级号（默认 4），最后一位是 1~9 时，
      在最后一位前补 0。
例：202404041  -> 2024040401
    2024040410 -> 2024040410（不变）

新增：可以从学号里删除指定字符（默认 - + _），并顺手处理
      空格/Tab、全角数字、零宽字符；支持去重和“仅清理”模式。

运行：python main.py（或用 run.bat 双击启动）
"""

import json
import os
import sys
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from student_id_core import (
    APP_VERSION,
    DEFAULT_STRIP_CHARS,
    NAME_FORMATS,
    NAME_FORMAT_SPLIT,
    ProcessResult,
    is_valid_class_no,
    process_text,
    read_paths,
    read_text_file,
)
from file_drop import FileDropTarget

APP_TITLE = "学号补零工具"

#: 下拉框显示的文字 → 核心里的格式代码
NAME_FORMAT_BY_LABEL = {label: code for code, label in NAME_FORMATS}

SAMPLE_TEXT = "\n".join([
    "202404041",
    "2024-0404-2",
    "2024040+43",
    "20240404_4",
    "2024　0404　6",   # 全角空格
    "2024040410",
    "202404041",       # 与第一行重复，勾选“去重”时会被跳过
    "学号202404047",   # 非数字，原样保留
])


def _app_dir() -> str:
    """程序所在目录（打包成 exe 后取 exe 目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


SETTINGS_FILE = os.path.join(_app_dir(), "settings.json")


def _resource_path(name: str) -> str:
    """资源文件路径：源码运行时在程序目录，打包后从 PyInstaller 的临时目录取。"""
    base = getattr(sys, "_MEIPASS", _app_dir())
    return os.path.join(base, name)


def enable_high_dpi() -> None:
    """Windows 高分屏下让界面清晰（需要在创建窗口前调用）。"""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.last_result: ProcessResult | None = None

        self.class_var = tk.StringVar(value="4")
        self.strip_var = tk.StringVar(value=DEFAULT_STRIP_CHARS)
        self.whitespace_var = tk.BooleanVar(value=True)
        self.halfwidth_var = tk.BooleanVar(value=True)
        self.dedupe_var = tk.BooleanVar(value=False)
        self.from_names_var = tk.BooleanVar(value=True)
        self.name_format_var = tk.StringVar(value=NAME_FORMATS[0][1])
        # 保存位置：source=源文件夹 / subfolder=源文件夹下新建子文件夹 / choose=每次选择
        self.save_mode_var = tk.StringVar(value="source")
        # 最近一次导入所在的文件夹（“保存到源文件夹”用）
        self.source_dir = ""

        self._setup_style()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.geometry("1000x680")
        self.minsize(860, 520)
        try:
            self.iconbitmap(_resource_path("app-icon.ico"))
        except tk.TclError:
            pass
        self.report_callback_exception = self._on_callback_error

        self._build_menu()
        self._build_settings()
        self._build_texts()
        self._build_buttons()
        self._build_status()
        self._bind_shortcuts()

        self._load_settings()
        self.class_var.trace_add("write", lambda *_: self._update_rule_hint())
        self._update_rule_hint()
        self._center_window()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # 支持用鼠标把文件/文件夹拖进窗口
        self.drop_target = FileDropTarget(self, self._on_drop)
        self.after(150, self._enable_file_drop)

    # ---------- 界面搭建 ----------
    def _setup_style(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        font = ("Microsoft YaHei UI", 10)
        self.option_add("*Font", font)
        style.configure("TLabel", font=font)
        style.configure("TLabelframe.Label", font=font)
        style.configure("Hint.TLabel", foreground="#666666", font=("Microsoft YaHei UI", 9))
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", foreground="#0077cc", font=font)
        style.configure("Warn.TLabel", foreground="#b35c00", font=font)

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="导入文件…（可多选）", accelerator="Ctrl+O", command=self.do_import)
        file_menu.add_command(label="导入文件夹…", accelerator="Ctrl+Shift+O",
                              command=self.do_import_folder)
        file_menu.add_command(label="保存结果…", accelerator="Ctrl+S", command=self.do_save)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="复制结果", accelerator="Ctrl+Shift+C", command=self.do_copy)
        edit_menu.add_command(label="清空输入和结果", command=self.do_clear)
        menubar.add_cascade(label="编辑", menu=edit_menu)

        tool_menu = tk.Menu(menubar, tearoff=False)
        tool_menu.add_command(label="一键补零", accelerator="F5", command=self.do_fix)
        tool_menu.add_command(label="仅清理字符", accelerator="Ctrl+L", command=self.do_clean)
        tool_menu.add_separator()
        tool_menu.add_command(label="恢复默认删除字符", command=self.reset_strip_chars)
        tool_menu.add_command(label="填入示例数据", command=self.fill_sample)
        tool_menu.add_command(label="查看非数字行", command=self.show_issues)
        menubar.add_cascade(label="工具", menu=tool_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

    def _build_settings(self):
        box = ttk.LabelFrame(self, text="设置", padding=(10, 6, 10, 8))
        box.pack(fill="x", padx=10, pady=(10, 6))
        box.columnconfigure(2, weight=1)

        ttk.Label(box, text="班级号（倒数第二位）：").grid(row=0, column=0, sticky="w")
        self.class_entry = ttk.Entry(box, textvariable=self.class_var, width=4, justify="center")
        self.class_entry.grid(row=0, column=1, sticky="w", padx=(0, 12))
        self.class_entry.bind("<Return>", lambda _e: self.do_fix())

        self.rule_label = ttk.Label(box, text="", style="Hint.TLabel")
        self.rule_label.grid(row=0, column=2, columnspan=3, sticky="w")

        ttk.Label(box, text="要删除的字符：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        strip_row = ttk.Frame(box)
        strip_row.grid(row=1, column=1, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Entry(strip_row, textvariable=self.strip_var, width=18).pack(side="left")
        ttk.Label(strip_row, text="（可填多个，例如 -+_ 或 /）", style="Hint.TLabel").pack(
            side="left", padx=(6, 10))
        ttk.Button(strip_row, text="恢复默认", width=9,
                   command=self.reset_strip_chars).pack(side="left")

        # 选项单独一行，避免窗口窄时被裁掉
        opt_row = ttk.Frame(box)
        opt_row.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opt_row, text="删除空格和 Tab", variable=self.whitespace_var).pack(
            side="left")
        ttk.Checkbutton(opt_row, text="全角数字转半角", variable=self.halfwidth_var).pack(
            side="left", padx=(16, 0))
        ttk.Checkbutton(opt_row, text="结果去重（保留首次出现）", variable=self.dedupe_var).pack(
            side="left", padx=(16, 0))

        # 拖入 Word/Excel 作业时，从文件名里取学号
        name_row = ttk.Frame(box)
        name_row.grid(row=3, column=0, columnspan=5, sticky="w", pady=(4, 0))
        ttk.Checkbutton(name_row, text="Word/Excel 作业从文件名取学号",
                        variable=self.from_names_var).pack(side="left")
        ttk.Label(name_row, text="输出格式：", style="Hint.TLabel").pack(side="left", padx=(14, 4))
        self.format_box = ttk.Combobox(
            name_row, textvariable=self.name_format_var, state="readonly", width=34,
            values=[label for _, label in NAME_FORMATS])
        self.format_box.pack(side="left")

        # 保存位置：默认存回源文件夹
        save_row = ttk.Frame(box)
        save_row.grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(save_row, text="保存到：").pack(side="left")
        ttk.Radiobutton(save_row, text="源文件夹", value="source", variable=self.save_mode_var).pack(
            side="left", padx=(4, 10))
        ttk.Radiobutton(save_row, text="源文件夹下新建「已处理」", value="subfolder",
                        variable=self.save_mode_var).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(save_row, text="每次选择文件夹", value="choose",
                        variable=self.save_mode_var).pack(side="left")

    def _build_texts(self):
        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=6)

        left = ttk.LabelFrame(paned, text="原始学号（每行一个，可直接粘贴，或把文件/文件夹拖进来）")
        right = ttk.LabelFrame(paned, text="处理结果（可直接编辑）")
        paned.add(left, weight=1)
        paned.add(right, weight=1)

        self.in_text = self._make_text_area(left, undo=True)
        self.out_text = self._make_text_area(right, undo=False)

    def _make_text_area(self, parent: ttk.LabelFrame, *, undo: bool) -> tk.Text:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        text = tk.Text(frame, wrap="none", font=("Consolas", 11), undo=undo,
                       width=1, height=6,
                       relief="flat", highlightthickness=1,
                       highlightbackground="#c8c8c8", highlightcolor="#0077cc")
        ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)

        text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")

        # Ctrl+A 全选（Windows 下 Tk 默认是“移到行首”）
        text.bind("<Control-a>", self._select_all)
        text.bind("<Control-A>", self._select_all)
        return text

    @staticmethod
    def _select_all(event):
        event.widget.tag_add("sel", "1.0", "end-1c")
        event.widget.mark_set("insert", "1.0")
        event.widget.see("insert")
        return "break"

    def _build_buttons(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10)

        ttk.Button(bar, text="导入文件", command=self.do_import).pack(side="left")
        ttk.Button(bar, text="一键补零 (F5)", style="Primary.TButton",
                   command=self.do_fix).pack(side="left", padx=6)
        ttk.Button(bar, text="仅清理字符", command=self.do_clean).pack(side="left")
        ttk.Button(bar, text="复制结果", command=self.do_copy).pack(side="left", padx=6)
        ttk.Button(bar, text="保存结果", command=self.do_save).pack(side="left")
        ttk.Button(bar, text="清空", command=self.do_clear).pack(side="left", padx=6)

    def _build_status(self):
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10, pady=(6, 9))
        self.status = ttk.Label(bar, text="就绪：左侧粘贴学号后点「一键补零」", style="Status.TLabel")
        self.status.pack(side="left")
        self.counter = ttk.Label(bar, text="", style="Hint.TLabel")
        self.counter.pack(side="right", padx=(10, 0))
        # 非数字行检查放在状态栏，按钮栏就不会太挤
        self.issues_button = ttk.Button(bar, text="查看非数字行", command=self.show_issues)
        self.issues_button.pack(side="right")

    def _bind_shortcuts(self):
        self.bind("<F5>", lambda _e: self.do_fix())
        self.bind("<Control-Return>", lambda _e: self.do_fix())
        self.bind("<Control-o>", lambda _e: self.do_import())
        self.bind("<Control-Shift-O>", lambda _e: self.do_import_folder())
        self.bind("<Control-s>", lambda _e: self.do_save())
        self.bind("<Control-l>", lambda _e: self.do_clean())
        self.bind("<Control-Shift-C>", lambda _e: self.do_copy())

    def _center_window(self):
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ---------- 设置 ----------
    def _update_rule_hint(self):
        class_no = self.class_var.get().strip() or "?"
        self.rule_label.config(
            text=f"规则：不足 10 位的 {class_no}1~{class_no}9 → {class_no}01~{class_no}09，其他不变")

    def reset_strip_chars(self):
        self.strip_var.set(DEFAULT_STRIP_CHARS)
        self.whitespace_var.set(True)
        self.halfwidth_var.set(True)
        self.set_status("已恢复默认删除字符：" + DEFAULT_STRIP_CHARS)

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(cfg, dict):
            return

        class_no = str(cfg.get("class_no", "4"))
        if is_valid_class_no(class_no):
            self.class_var.set(class_no)
        self.strip_var.set(str(cfg.get("strip_chars", DEFAULT_STRIP_CHARS)))
        self.whitespace_var.set(bool(cfg.get("remove_whitespace", True)))
        self.halfwidth_var.set(bool(cfg.get("halfwidth", True)))
        self.dedupe_var.set(bool(cfg.get("dedupe", False)))
        self.from_names_var.set(bool(cfg.get("from_names", True)))
        fmt = cfg.get("name_format")
        labels = {code: label for code, label in NAME_FORMATS}
        if fmt in labels:
            self.name_format_var.set(labels[fmt])
        if cfg.get("save_mode") in ("source", "subfolder", "choose"):
            self.save_mode_var.set(cfg["save_mode"])

    def _save_settings(self):
        cfg = {
            "class_no": self.class_var.get().strip(),
            "strip_chars": self.strip_var.get(),
            "remove_whitespace": self.whitespace_var.get(),
            "halfwidth": self.halfwidth_var.get(),
            "dedupe": self.dedupe_var.get(),
            "from_names": self.from_names_var.get(),
            "name_format": NAME_FORMAT_BY_LABEL.get(
                self.name_format_var.get(), NAME_FORMAT_SPLIT),
            "save_mode": self.save_mode_var.get(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    # ---------- 事件 ----------
    def _read_options(self, *, need_class: bool) -> dict | None:
        class_no = self.class_var.get().strip()
        if need_class and not is_valid_class_no(class_no):
            messagebox.showwarning("提示", "班级号请填 1 位数字，例如 4")
            self.class_entry.focus_set()
            self.class_entry.selection_range(0, "end")
            return None
        if not is_valid_class_no(class_no):
            class_no = "4"
        return {
            "class_no": class_no,
            "strip_chars": self.strip_var.get(),
            "halfwidth": self.halfwidth_var.get(),
            "remove_whitespace": self.whitespace_var.get(),
            "dedupe": self.dedupe_var.get(),
        }

    def do_fix(self):
        opts = self._read_options(need_class=True)
        if opts is None:
            return
        src = self.in_text.get("1.0", "end-1c")
        if not src.strip():
            messagebox.showinfo("提示", "请先在左侧粘贴或导入学号")
            return
        self._run(src, pad=True, **opts)

    def do_clean(self):
        opts = self._read_options(need_class=False)
        if opts is None:
            return
        src = self.in_text.get("1.0", "end-1c")
        if not src.strip():
            messagebox.showinfo("提示", "请先在左侧粘贴或导入学号")
            return
        self._run(src, pad=False, **opts)

    def _run(self, src: str, *, pad: bool, **opts):
        result = process_text(src, **opts, pad=pad)
        self.last_result = result

        self.out_text.delete("1.0", "end")
        self.out_text.insert("1.0", result.output)

        if not result.lines:
            self.set_status("没有可处理的内容：清理后所有行都是空的", warn=True)
            self.counter.config(text="结果 0 行")
            return

        action = "一键补零完成" if pad else "仅清理完成"
        text = f"{action}：{result.summary()}"
        self.set_status(text, warn=bool(result.invalid))
        self.counter.config(text=f"结果 {len(result.lines)} 行")

    def do_import(self):
        paths = filedialog.askopenfilenames(
            title="导入学号文本（可多选）",
            filetypes=[("文本/CSV 文件", "*.txt *.csv *.tsv *.log *.md"),
                       ("所有文件", "*.*")])
        if paths:
            self._load_paths(list(paths))

    def do_import_folder(self):
        folder = filedialog.askdirectory(title="选择包含学号文本的文件夹")
        if folder:
            self._load_paths([folder])

    def _on_drop(self, paths):
        """窗口里被拖入文件/文件夹时的回调。"""
        self._load_paths(paths)

    def _load_paths(self, paths, *, confirm_replace: bool = True):
        opts = self._read_options(need_class=False) or {}
        bundle = read_paths(paths,
                            from_names=self.from_names_var.get(),
                            name_format=NAME_FORMAT_BY_LABEL.get(
                                self.name_format_var.get(), NAME_FORMAT_SPLIT),
                            class_no=opts.get("class_no", "4"))
        if bundle.source_dir:
            self.source_dir = bundle.source_dir
        if not bundle.text.strip():
            names = "、".join(os.path.basename(str(p)) for p in list(paths)[:3])
            message = "没有找到可读取的学号。"
            if names:
                message += f"\n\n拖入/选中的是：{names}"
            message += ("\n\n支持两种方式：\n"
                        "· 文本文件（.txt / .csv / .tsv / .log / .md）直接读内容；\n"
                        "· Word / Excel / PDF 等按文件名取学号，如 "
                        "`25179704-08-何金森-作业一.doc` → `2517970408 何金森 作业一`"
                        "（勾选「Word/Excel 作业从文件名取学号」）。")
            if not self.from_names_var.get():
                message += "\n\n当前没有勾选「Word/Excel 作业从文件名取学号」。"
            if bundle.errors:
                message += f"\n\n跳过 {len(bundle.errors)} 个，第一个原因：{bundle.errors[0][1]}"
            messagebox.showwarning("导入失败", message)
            return

        current = self.in_text.get("1.0", "end-1c")
        if confirm_replace and current.strip():
            question = (f"输入框里已有内容，要用这次导入的 {bundle.lines} 行"
                        f"（{bundle.summary()}）替换吗？")
            if not messagebox.askyesno("替换内容", question):
                return

        self.in_text.delete("1.0", "end")
        self.in_text.insert("1.0", bundle.text)
        self.set_status(f"已导入：{bundle.summary()}")

        if bundle.names:
            self.set_status(f"已导入：{bundle.summary()}（其中 {len(bundle.names)} 个学号来自文件名）")

        if bundle.errors and not bundle.files and not bundle.names:
            first_path, reason = bundle.errors[0]
            messagebox.showwarning(
                "部分文件已跳过",
                f"共 {len(bundle.errors)} 个文件没有导入，已跳过。\n\n第一个：\n{first_path}\n{reason}")

    def do_copy(self):
        result = self.out_text.get("1.0", "end-1c")
        if not result.strip():
            messagebox.showinfo("提示", "还没有结果可复制")
            return
        self.clipboard_clear()
        self.clipboard_append(result)
        self.set_status(f"已复制到剪贴板（{len(result.splitlines())} 行）")

    def do_save(self):
        result = self.out_text.get("1.0", "end-1c")
        if not result.strip():
            messagebox.showinfo("提示", "还没有结果可保存")
            return

        mode = self.save_mode_var.get()
        if mode == "choose":
            initial = self.source_dir or os.path.expanduser("~")
            path = filedialog.asksaveasfilename(
                title="保存结果",
                initialdir=initial,
                defaultextension=".txt",
                initialfile="学号处理结果.txt",
                filetypes=[("文本文件", "*.txt"), ("CSV 文件", "*.csv"), ("所有文件", "*.*")])
            if not path:
                return
        else:
            target_dir = self._save_dir(mode)
            if not target_dir:
                return
            try:
                os.makedirs(target_dir, exist_ok=True)
            except OSError as e:
                messagebox.showerror("保存失败", f"无法创建文件夹：\n{target_dir}\n\n{e}")
                return
            path = os.path.join(target_dir, "学号处理结果.txt")
            if os.path.exists(path) and not messagebox.askyesno(
                    "文件已存在", f"{path}\n\n要覆盖吗？"):
                return

        try:
            # utf-8-sig：Excel 直接打开不乱码，普通编辑器也正常
            with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(result.rstrip() + "\n")
        except OSError as e:
            messagebox.showerror("保存失败", str(e))
            return

        self.set_status(f"已保存：{path}")
        folder = os.path.dirname(path)
        if messagebox.askyesno("完成", f"已保存到：\n{path}\n\n要打开所在文件夹吗？"):
            self._open_folder(folder)

    def _save_dir(self, mode: str) -> str:
        """按“保存到”选项算出目标文件夹；源文件夹未知时让用户选一个。"""
        base = self.source_dir
        if not base or not os.path.isdir(base):
            base = filedialog.askdirectory(title="选择保存位置（还没导入文件夹）")
            if not base:
                return ""
            self.source_dir = base
        if mode == "subfolder":
            name = os.path.basename(os.path.normpath(base)) or "学号"
            return os.path.join(base, f"{name}-已处理")
        return base

    def _open_folder(self, folder: str):
        try:
            os.startfile(folder)  # type: ignore[attr-defined]  # Windows
        except (AttributeError, OSError):
            pass

    def do_clear(self):
        self.in_text.delete("1.0", "end")
        self.out_text.delete("1.0", "end")
        self.last_result = None
        self.counter.config(text="")
        self.set_status("就绪：左侧粘贴学号后点「一键补零」")

    def set_status(self, text: str, *, warn: bool = False):
        self.status.configure(text=text, style="Warn.TLabel" if warn else "Status.TLabel")

    # ---------- 辅助功能 ----------
    def show_issues(self):
        result = self.last_result
        if not result or not result.invalid:
            messagebox.showinfo("检查结果", "没有发现非数字行，全部内容都可正常处理。")
            return
        preview = [f"第 {lineno} 行：{text}" for lineno, text in result.invalid[:50]]
        extra = ""
        if len(result.invalid) > 50:
            extra = f"\n……共 {len(result.invalid)} 行，这里只显示前 50 行"
        messagebox.showwarning(
            "非数字行",
            "下面这些行清理后仍不是纯数字，已原样保留在结果里：\n\n"
            + "\n".join(preview) + extra)

    def fill_sample(self):
        self.in_text.delete("1.0", "end")
        self.in_text.insert("1.0", SAMPLE_TEXT)
        self.set_status("已填入示例数据，点「一键补零」看看效果")

    def show_help(self):
        messagebox.showinfo(
            "使用说明",
            "1. 把学号粘贴到左侧（每行一个，也支持从表格复制）。\n"
            "2. 也可以直接用鼠标把 .txt/.csv 文件、甚至整个文件夹拖进窗口；"
            "文件夹里所有文本文件会按文件名顺序合并导入（文件菜单里也有「导入文件夹」）。\n"
            "3. 需要时修改班级号（默认 4）；已经够 10 位的学号不会被重复补零。\n"
            "4. 「要删除的字符」里填要去掉的符号，默认 -+_；"
            "勾选「删除空格/Tab」可去掉空格和制表符。\n"
            "5. 点「一键补零」：先删字符，再按规则补 0；"
            "若只想删字符，点「仅清理字符」。\n"
            "6. 拖入作业文件夹时，学号取自文件名，后面的姓名/作业名按「输出格式」保留。\n"
            "7. 「保存到」默认存回源文件夹；也可建「已处理」子文件夹或每次选择。\n"
            "8. 右侧结果可复制或保存（Ctrl+Shift+C / Ctrl+S）。\n\n"
            "示例：2024-0404-1 → 202404041 → 2024040401")

    def show_about(self):
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE} v{APP_VERSION}\n\n"
            "规则：学号不足 10 位、倒数第二位是班级号、最后一位是 1~9 时，"
            "在最后一位前补 0。\n"
            "已经达到 10 位的学号原样保留，不会重复补零。\n"
            "例：202404041 → 2024040401\n\n"
            "纯 Python + Tkinter 编写，无需安装第三方库。")

    def _enable_file_drop(self):
        """窗口出现后再挂拖放钩子（子控件此时才创建齐）。"""
        try:
            if self.drop_target.enable():
                self.set_status("就绪：粘贴学号，或把文件/文件夹直接拖进窗口")
        except Exception:
            pass

    def _on_callback_error(self, exc, val, tb):
        traceback.print_exception(exc, val, tb)
        messagebox.showerror("出错了", f"程序遇到一个错误：\n{val}")

    def on_close(self):
        try:
            self.drop_target.close()
        except Exception:
            pass
        self._save_settings()
        self.destroy()


def main() -> None:
    enable_high_dpi()
    App().mainloop()


if __name__ == "__main__":
    main()
