# -*- coding: utf-8 -*-
"""给 Tk 窗口加上 Windows 原生的“拖文件/文件夹进来”支持。

用的是 ``WM_DROPFILES``（``DragAcceptFiles`` + 子类化窗口过程），
不需要第三方库。

实现上有两个坑，这里都绕开了：

1. **不能在窗口过程里调用 Tk/Tcl 的 API**（``root.after``、读写控件等）。
   真实拖放时 Explorer 还在自己的 OLE 拖拽循环里，这时回调到 Tcl 很容易
   卡死；所以窗口过程里只做 ``DragQueryFileW`` + ``DragFinish``，
   把路径丢进一个普通队列，由主线程定时轮询后再回调。
2. 子类化必须保存旧的窗口过程，并在处理完后转发所有其它消息，
   否则 Tk 的控件会直接失灵。

用法::

    target = FileDropTarget(root, on_drop)
    target.enable()      # 窗口显示出来以后调用
    ...
    target.close()       # 退出时恢复原来的窗口过程
"""

from __future__ import annotations

import ctypes
import sys
from collections import deque
from ctypes import wintypes

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
POLL_MS = 100

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:  # pragma: no cover - 只在 Windows 上执行
    _user32 = ctypes.windll.user32
    _shell32 = ctypes.windll.shell32

    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    _user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t

    _shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    _shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    _shell32.DragQueryFileW.restype = wintypes.UINT
    _shell32.DragFinish.argtypes = [wintypes.HANDLE]
    _shell32.DragFinish.restype = None

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _set_window_long = _user32.SetWindowLongPtrW
    else:  # 32 位 Python 没有 SetWindowLongPtrW
        _set_window_long = _user32.SetWindowLongW
    _set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    _set_window_long.restype = ctypes.c_void_p


class FileDropTarget:
    """把窗口（含所有子控件）变成文件拖放目标。"""

    def __init__(self, root, callback):
        self.root = root
        self.callback = callback
        self._old_procs: dict[int, int] = {}
        self._pending: deque[list[str]] = deque()
        self._poll_id = None
        self._proc = _WNDPROC(self._wndproc) if _IS_WINDOWS else None
        self.enabled = False

    # ---------- 对外 ----------
    def enable(self) -> bool:
        """开始接收拖放；返回是否成功。"""
        if not _IS_WINDOWS or self.enabled:
            return False
        top = int(self.root.wm_frame(), 16)
        for hwnd in [top, *self._descendants(top)]:
            self._hook(hwnd)
        self.enabled = True
        self._schedule_poll()
        return True

    def close(self) -> None:
        """恢复原来的窗口过程。"""
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if not self.enabled:
            return
        for hwnd, old in list(self._old_procs.items()):
            try:
                _set_window_long(wintypes.HWND(hwnd), GWLP_WNDPROC, ctypes.c_void_p(old))
                _shell32.DragAcceptFiles(wintypes.HWND(hwnd), False)
            except Exception:
                pass
        self._old_procs.clear()
        self.enabled = False

    # ---------- 轮询（主线程，可以安全调用 Tk） ----------
    def _schedule_poll(self) -> None:
        try:
            self._poll_id = self.root.after(POLL_MS, self._poll)
        except Exception:
            self._poll_id = None

    def _poll(self) -> None:
        self._poll_id = None
        while self._pending:
            paths = self._pending.popleft()
            try:
                self.callback(paths)
            except Exception as exc:  # 回调出错不要让轮询停掉
                print(f"拖放回调出错：{exc!r}", file=sys.stderr)
        if self.enabled:
            self._schedule_poll()

    # ---------- 内部 ----------
    def _descendants(self, hwnd: int) -> list[int]:
        found: list[int] = []

        def _collect(child, _param):
            found.append(int(child))
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_collect)
        _user32.EnumChildWindows(wintypes.HWND(hwnd), callback, 0)
        return found

    def _hook(self, hwnd: int) -> None:
        if not hwnd or hwnd in self._old_procs:
            return
        old = _set_window_long(wintypes.HWND(hwnd), GWLP_WNDPROC,
                               ctypes.cast(self._proc, ctypes.c_void_p))
        old_value = int(old or 0) if old is not None else 0
        if not old_value:  # 子类化失败，别记下来（否则转发时无从调用）
            return
        self._old_procs[hwnd] = old_value
        _shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            # 这里只做纯 Win32 的事：读路径、释放句柄，绝不碰 Tk
            try:
                paths = self._read_paths(wparam)
            except Exception:
                paths = []
            finally:
                try:
                    _shell32.DragFinish(wintypes.HANDLE(wparam))
                except Exception:
                    pass
            if paths:
                self._pending.append(paths)
            return 0

        old = self._old_procs.get(int(hwnd))
        if old:
            try:
                return _user32.CallWindowProcW(
                    ctypes.c_void_p(old), hwnd, msg, wparam, lparam)
            except Exception:
                pass
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    @staticmethod
    def _read_paths(hdrop) -> list[str]:
        handle = wintypes.HANDLE(hdrop)
        count = _shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
        paths: list[str] = []
        for index in range(count):
            length = _shell32.DragQueryFileW(handle, index, None, 0)
            if not length:
                continue
            buffer = ctypes.create_unicode_buffer(length + 1)
            _shell32.DragQueryFileW(handle, index, buffer, length + 1)
            if buffer.value:
                paths.append(buffer.value)
        return paths
