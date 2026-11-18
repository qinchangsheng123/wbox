# -*- coding: utf-8 -*-
"""窗口监听器 v2：不过滤标题，连不可见的窗口一起跟踪，用来定位 CEF 弹窗。

相比 v1 的改动：
  - 不再要求「有标题」（CEF 弹窗常常是无标题窗口，v1 因此漏抓）
  - 记录窗口的出现/消失、样式、owner、根祖先，便于判断是独立顶层窗还是主窗子窗
  - 同一 hwnd 从 hidden 变 visible 也记为「出现」（WeGame 主窗会复用 hwnd）

只读，不动任何东西。用法：python test/window_watch2.py 1200
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import popup_guard as pg

u32 = ctypes.WinDLL('user32', use_last_error=True)
u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetWindowTextLengthW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetWindow.restype = wintypes.HWND

GWL_EXSTYLE = -20
GA_ROOT = 2
GW_OWNER = 4
GA_PARENT = 1

# 名字里带这些的进程才关注（腾讯系 / 游戏本体）
WATCH = ('wegame', 'tcls', 'tenio', 'tencent', 'tgame', 'browser',
         'cef', 'live', 'txgame', 'txplatform', 'webgame', 'douyu', 'qq')


class RECT(ctypes.Structure):
    _fields_ = [('l', wintypes.LONG), ('t', wintypes.LONG),
                ('r', wintypes.LONG), ('b', wintypes.LONG)]


def wtext(h):
    n = u32.GetWindowTextLengthW(h)
    if n <= 0:
        return ''
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(h, b, n + 1)
    return b.value


def wclass(h):
    b = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(h, b, 256)
    return b.value


def wrect(h):
    r = RECT()
    u32.GetWindowRect(h, ctypes.byref(r))
    return r.r - r.l, r.b - r.t


def entry(h):
    """返回该窗口的可读描述，附带关注等级。"""
    p = wintypes.DWORD(0)
    u32.GetWindowThreadProcessId(h, ctypes.byref(p))
    pid = int(p.value)
    path = pg.process_path(pid)
    name = os.path.basename(path).lower() if path else ''
    w, hh = wrect(h)
    ex = u32.GetWindowLongW(h, GWL_EXSTYLE) & 0xFFFFFFFF
    owner = int(u32.GetWindow(h, GW_OWNER) or 0)
    root = int(u32.GetAncestor(h, GA_ROOT) or 0)
    flags = []
    if owner:
        flags.append('owned=%d' % owner)
    if root and root != int(h):
        flags.append('root=%d' % root)
    if ex & 0x00000080:
        flags.append('tool')
    if ex & 0x00080000:
        flags.append('layered')
    if ex & 0x00000008:
        flags.append('topmost')
    return {
        'hwnd': int(h), 'pid': pid, 'name': name, 'path': path,
        'cls': wclass(h), 'title': wtext(h), 'w': w, 'h': hh,
        'ex': ex, 'flags': ','.join(flags),
    }


def interesting(e):
    n = e['name']
    if not n:
        return False
    return any(k in n for k in WATCH)


def _safe(s, n):
    """游戏窗口标题常含半截 UTF-8（某些游戏主窗口就有一条，带孤立 surrogate），
    直接 print 会抛 UnicodeEncodeError 把整个扫描循环打断。转成可打印文本再截断。"""
    if not s:
        return ''
    try:
        s = s.encode('utf-8', 'backslashreplace').decode('utf-8', 'replace')
    except Exception:
        s = repr(s)
    return s[:n]


def fmt(e):
    return ('%s | pid=%-6d %-22s %4dx%-4d cls=%-28s ex=0x%08X %-28s | %s'
            % (time.strftime('%H:%M:%S'), e['pid'], _safe(e['name'], 22), e['w'], e['h'],
               _safe(e['cls'], 28), e['ex'], e['flags'][:28], _safe(e['title'], 50)))


def visible_set():
    """所有可见窗口的 hwnd 集合（含无标题）。"""
    out = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if u32.IsWindowVisible(h):
            out.add(int(h))
        return True

    u32.EnumWindows(cb, 0)
    return out


def snapshot_all():
    rows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if u32.IsWindowVisible(h):
            e = entry(h)
            if interesting(e):
                rows.append(e)
        return True

    u32.EnumWindows(cb, 0)
    return rows


DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'window_watch2.log')
open(OUT, 'w', encoding='utf-8').close()


def out(line):
    print(line, flush=True)
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


prev = visible_set()
out('监听开始（含无标题窗口）：当前可见 %d 个，关注中的腾讯系/游戏窗口：' % len(prev))
for e in snapshot_all():
    out('  ' + fmt(e))

start = time.time()
while time.time() - start < DURATION:
    try:
        cur = visible_set()
        added = cur - prev
        gone = prev - cur
        for h in sorted(added):
            e = entry(h)
            if not interesting(e) and e['w'] < 200:
                continue
            out('++ %s' % fmt(e))
        for h in sorted(gone):
            e = entry(h)
            if interesting(e):
                out('-- %s' % fmt(e))
        prev = cur
    except Exception as ex:
        out('扫描异常: %r' % (ex,))
    time.sleep(0.25)

out('监听结束')
