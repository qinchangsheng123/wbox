# -*- coding: utf-8 -*-
"""全量窗口倾倒：连无标题、不可见的窗口一起列出来，用来定位 CEF 弹窗属于哪个进程。

输出：hwnd / pid / 进程名 / 类名 / 标题 / 尺寸 / 可见性 / 顶层或子窗口 / owner / 扩展样式
只读，不动任何东西。
"""
import ctypes
import os
import sys
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import popup_guard as pg

u32 = ctypes.WinDLL('user32', use_last_error=True)
u32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetWindow.restype = wintypes.HWND
u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetParent.argtypes = [wintypes.HWND]
u32.GetParent.restype = wintypes.HWND
u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetWindowTextLengthW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]

GWL_STYLE = -16
GWL_EXSTYLE = -20
GA_ROOT = 2
GW_OWNER = 4


class RECT(ctypes.Structure):
    _fields_ = [('l', wintypes.LONG), ('t', wintypes.LONG),
                ('r', wintypes.LONG), ('b', wintypes.LONG)]


def text(h):
    n = u32.GetWindowTextLengthW(h)
    if n <= 0:
        return ''
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(h, b, n + 1)
    return b.value


def cls(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def rect(h):
    r = RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r.r - r.l, r.b - r.t


def pid_of(h):
    p = wintypes.DWORD(0)
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    return int(p.value)


def dump(h, indent, child=False):
    pid = pid_of(h)
    path = pg.process_path(pid)
    name = os.path.basename(path) if path else '?'
    w, hgt = rect(h)
    style = u32.GetWindowLongW(h, GWL_STYLE) & 0xFFFFFFFF
    ex = u32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
    owner = u32.GetWindow(h, GW_OWNER)
    top = u32.GetAncestor(h, GA_ROOT)
    flags = []
    if not u32.IsWindowVisible(h):
        flags.append('hidden')
    if owner:
        flags.append('owned')
    if top and int(top) != int(h):
        flags.append('child-of-%d' % int(top))
    if ex & 0x00000080:
        flags.append('toolwindow')
    if ex & 0x00080000:
        flags.append('layered')
    if ex & 0x00000008:
        flags.append('topmost')
    print('%s hwnd=%-10d pid=%-6d %-24s %4dx%-4d cls=%-32s style=0x%08X ex=0x%08X %s | %s'
          % ('  ' * indent, int(h), pid, name[:24], w, hgt, cls(h)[:32], style, ex,
             ','.join(flags), text(h)[:50]))
    if not child:
        subs = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(ch, _l):
            subs.append(ch)
            return True

        u32.EnumChildWindows(h, ctypes.cast(cb, ctypes.c_void_p), 0)
        for ch in subs:
            dump(ch, indent + 1, child=True)


if __name__ == '__main__':
    roots = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(h, _l):
        roots.append(h)
        return True

    u32.EnumWindows(enum_cb, 0)
    print('=== 顶层窗口共 %d 个，按进程名过滤可疑项 ===' % len(roots))
    for h in roots:
        pid = pid_of(h)
        path = pg.process_path(pid)
        name = os.path.basename(path).lower() if path else ''
        if not name:
            continue
        if any(k in name for k in ('wegame', 'tcls', 'tenio', 'tencent', 'tgame',
                                   'qq', 'cef', 'live', 'txgame', 'txplatform',
                                   'webgame', 'douyu')):
            dump(h, 0)
