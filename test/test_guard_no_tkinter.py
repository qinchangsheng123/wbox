# -*- coding: utf-8 -*-
"""规范A 防回归：守护侧模块链不得把 tkinter / settings_gui 拉进进程。

v2.5.0 曾由心跳版本号 _beat_version import settings_gui，使 _tkinter/tcl86t/tk86t
常驻守护（实测 WS +10 MB 量级）。版本号单一来源迁到 core_defaults 后，用本测试
钉死：干净子进程里 import 守护侧全部模块 + 走一遍心跳 payload / 崩溃版本号，
Python 模块表与原生模块表都不许出现 tkinter 系。

为什么用子进程：run_all_inproc 同进程里先跑的 GUI 测试会留下 tkinter 模块缓存，
在父进程里断言 sys.modules 会把别人的污染算到守护头上。

用法: C:\\Python38\\python.exe test\\test_guard_no_tkinter.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src')

CHILD = r'''
import ctypes, json, sys, types
from ctypes import wintypes
sys.path.insert(0, sys.argv[1])
import core_defaults, crash_log, guard_core, guard_beat, proc_events
import ace_native, popup_guard, game_lib, plugin_host, plugin_package
g = types.SimpleNamespace(host=types.SimpleNamespace(registry=[]),
                          session_state='idle', tick_seq=0, session_started_at=0.0)
payload = guard_core._beat_payload(g)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
h = ctypes.windll.kernel32.GetCurrentProcess()
arr = (ctypes.c_void_p * 2048)()
need = wintypes.DWORD()
names = []
if psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 0x03):
    buf = ctypes.create_unicode_buffer(260)
    for i in range(need.value // ctypes.sizeof(ctypes.c_void_p)):
        if psapi.GetModuleBaseNameW(h, arr[i], buf, 260):
            names.append(buf.value.lower())
print(json.dumps({
    'version_ok': payload['version'] == core_defaults.VERSION
                  and crash_log._version() == core_defaults.VERSION,
    'py': sorted(set(sys.modules) & {'tkinter', 'settings_gui', '_tkinter'}),
    'native': sorted(n for n in names
                     if n in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll')),
}))
'''

PASSED = []
FAILED = []


def step(ok, label):
    (PASSED if ok else FAILED).append(label)
    print('[%s] %s' % ('ok' if ok else 'FAIL', label))


r = subprocess.run([sys.executable, '-c', CHILD, SRC],
                   capture_output=True, text=True)
step(r.returncode == 0, '干净子进程跑完守护侧模块链（rc=%d）' % r.returncode)
if r.returncode != 0:
    print(r.stderr[-2000:])
else:
    import json
    d = json.loads(r.stdout.strip().splitlines()[-1])
    step(d['version_ok'], '心跳 / 崩溃版本号均取自 core_defaults')
    step(not d['py'], 'Python 模块表无 tkinter 系: %s' % (d['py'] or 'clean'))
    step(not d['native'], '原生模块表无 tkinter 系: %s' % (d['native'] or 'clean'))

print('--- %d ok / %d FAIL ---' % (len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)