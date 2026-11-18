# -*- coding: utf-8 -*-
"""窗口监听器：把新出现的可见窗口记下来（进程名 / 路径 / 类名 / 标题 / 尺寸）。

用途：定位游戏退出后弹出的直播窗口到底属于哪个进程。
不杀任何东西，只读。
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import popup_guard as pg

u32 = ctypes.WinDLL('user32')

class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


def rect_of(hwnd):
    r = RECT()
    if u32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.right - r.left, r.bottom - r.top
    return 0, 0


DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'window_watch.log')

seen = set(w[0] for w in pg.enum_windows())
open(OUT, 'w', encoding='utf-8').close()
print('监听开始：基线 %d 个窗口，持续 %d 秒' % (len(seen), DURATION), flush=True)
print('基线窗口：', flush=True)
for hwnd, pid, cls, title in pg.enum_windows():
    print('   pid=%d cls=%s | %s' % (pid, cls, title[:40]), flush=True)

start = time.time()
while time.time() - start < DURATION:
    try:
        for hwnd, pid, cls, title in pg.enum_windows():
            if hwnd in seen:
                continue
            seen.add(hwnd)
            path = pg.process_path(pid)
            name = os.path.basename(path).lower() if path else '?'
            w, h = rect_of(hwnd)
            line = '%s | pid=%-6d | %-22s | %4dx%-4d | cls=%-22s | title=%s | path=%s' % (
                time.strftime('%H:%M:%S'), pid, name, w, h, cls, title[:60], path)
            print(line, flush=True)
            with open(OUT, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except Exception as e:
        print('扫描异常: %r' % (e,), flush=True)
    time.sleep(0.3)

print('监听结束，共记录 %d 个新窗口' % (len(seen) - 0), flush=True)
